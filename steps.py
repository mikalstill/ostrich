import curses
import datetime
import fcntl
import json
import os
import psutil
import re
import select
import shutil
import subprocess
import sys
import textwrap
import time
import yaml

import emitters


def _handle_path_in_cwd(path, cwd):
    if not cwd:
        return path
    if path.startswith('/'):
        return path
    return os.path.join(cwd, path)


class Step(object):
    def __init__(self, name, **kwargs):
        self.name = name
        self.depends = kwargs.get('depends', None)
        self.attempts = 0
        self.max_attempts = kwargs.get('max_attempts', 5)
        self.failing_step_delay = kwargs.get('failing_step_delay', 300)

    def run(self, emit, screen):
        if self.attempts > 0:
            emit.emit('... not our first attempt, sleeping for %s seconds'
                      % self.failing_step_delay)
            time.sleep(self.failing_step_delay)

        self.attempts += 1

        if self.attempts > self.max_attempts:
            emit.emit('... repeatedly failed step, giving up')
            sys.exit(1)

        return self._run(emit, screen)


class SimpleCommandStep(Step):
    def __init__(self, name, command, **kwargs):
        super(SimpleCommandStep, self).__init__(name, **kwargs)
        self.command = command
        self.cwd = kwargs.get('cwd')

        self.env = os.environ
        self.env.update(kwargs.get('env'))

    def __str__(self):
        return 'step %s, depends on %s' % (self.name, self.depends)

    def _run(self, emit, screen):
        emit.emit('Running %s' % self)
        emit.emit('# %s\n' % self.command)

        obj = subprocess.Popen(self.command,
                               stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               shell=True,
                               cwd=self.cwd,
                               env=self.env)
        proc = psutil.Process(obj.pid)
        procs = {}

        flags = fcntl.fcntl(obj.stdout, fcntl.F_GETFL)
        fcntl.fcntl(obj.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        flags = fcntl.fcntl(obj.stderr, fcntl.F_GETFL)
        fcntl.fcntl(obj.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        obj.stdin.close()
        while obj.poll() is None:
            readable, _, _ = select.select([obj.stderr, obj.stdout], [], [], 1)
            for f in readable:
                emit.emit(os.read(f.fileno(), 10000))

            seen = []
            for child in proc.children(recursive=True):
                try:
                    seen.append(child.pid)
                    if child.pid not in procs:
                        procs[child.pid] = ' '.join(child.cmdline())
                        emit.emit('*** process started *** %d -> %s'
                                  % (child.pid, procs[child.pid]))
                except psutil.NoSuchProcess:
                    pass

            ended = []
            for pid in procs:
                if pid not in seen:
                    emit.emit('*** process ended *** %d -> %s'
                              % (pid, procs.get(child.pid, '???')))
                    ended.append(pid)

            for pid in ended:
                del procs[pid]

        emit.emit('... process complete')
        returncode = obj.returncode
        emit.emit('... exit code %d' % returncode)
        return returncode == 0


class QuestionStep(Step):
    def __init__(self, name, title, helpful, prompt, **kwargs):
        super(QuestionStep, self).__init__(name, **kwargs)
        self.title = title
        self.help = helpful
        self.prompt = prompt

    def _run(self, emit, screen):
        emit.emit('%s' % self.title)
        emit.emit('%s\n' % ('=' * len(self.title)))
        emit.emit('%s\n' % self.help)
        return emit.getstr('>> ')


class RegexpEditorStep(Step):
    def __init__(self, name, path, search, replace, **kwargs):
        super(RegexpEditorStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.search = search
        self.replace = replace

    def _run(self, emit, screen):
        output = []
        changes = 0

        emit.emit('--- %s' % self.path)
        emit.emit('+++ %s' % self.path)

        with open(self.path, 'r') as f:
            for line in f.readlines():
                line = line.rstrip()
                newline = re.sub(self.search, self.replace, line)
                output.append(newline)

                if newline != line:
                    emit.emit('- %s' % line)
                    emit.emit('+ %s' % newline)
                    changes += 1
                else:
                    emit.emit('  %s' % line)

        with open(self.path, 'w') as f:
            f.write('\n'.join(output))

        return 'Changed %d lines' % changes


class BulkRegexpEditorStep(Step):
    def __init__(self, name, path, file_filter, replacements, **kwargs):
        super(BulkRegexpEditorStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.file_filter = re.compile(file_filter)
        self.replacements = replacements

    def _run(self, emit, screen):
        silent_emitter = emitters.NoopEmitter('noop', None)
        changes = 0

        for root, _, files in os.walk(self.path):
            for filename in files:
                m = self.file_filter.match(filename)
                if not m:
                    continue

                path = os.path.join(root, filename)
                for (search, replace) in self.replacements:
                    s = RegexpEditorStep('bulk-edit', path, search, replace)
                    result = s.run(silent_emitter, None)
                    emit.emit('%s -> %s' % (path, result))
                    if result != 'Changed 0 lines':
                        changes += 1

        return changes



class FileAppendStep(Step):
    def __init__(self, name, path, text, **kwargs):
        super(FileAppendStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.text = text

    def _run(self, emit, screen):
        if not os.path.exists(self.path):
            emit.emit('%s does not exist' % self.path)
            return False

        with open(self.path, 'a+') as f:
            f.write(self.text)
        return True



class CopyFileStep(Step):
    def __init__(self, name, from_path, to_path, **kwargs):
        super(CopyFileStep, self).__init__(name, **kwargs)
        self.from_path = _handle_path_in_cwd(from_path, kwargs.get('cwd'))
        self.to_path = _handle_path_in_cwd(to_path, kwargs.get('cwd'))

    def _run(self, emit, screen):
        shutil.copyfile(self.from_path, self.to_path)
        return True


class YamlAddElementStep(Step):
    def __init__(self, name, path, target_element_path, data, **kwargs):
        super(YamlAddElementStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.target_element_path = target_element_path
        self.data = data

    def _run(self, emit, screen):
        with open(self.path) as f:
            y = yaml.load(f.read())

        sub = y

        for key in self.target_element_path:
            sub = sub[key]

        sub.append(self.data)

        emit.emit('YAML after changes:')
        emit.emit(yaml.dump(y))

        with open(self.path, 'w') as f:
            f.write(yaml.dump(y, default_flow_style=False))

        return True


class YamlUpdateDictionaryStep(Step):
    def __init__(self, name, path, target_element_path, data, **kwargs):
        super(YamlUpdateDictionaryStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.target_element_path = target_element_path
        self.data = data

    def _run(self, emit, screen):
        with open(self.path) as f:
            y = yaml.load(f.read())

        sub = y

        for key in self.target_element_path:
            sub = sub[key]

        sub.update(self.data)

        emit.emit('YAML after changes:')
        emit.emit(yaml.dump(y))

        with open(self.path, 'w') as f:
            f.write(yaml.dump(y, default_flow_style=False))

        return True
