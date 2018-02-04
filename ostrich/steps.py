#!/usr/bin/env python
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import fcntl
import json
import os
import psutil
import re
import select
import shutil
import subprocess
import sys
import time
import yaml

import emitters
import utils


def _handle_path_in_cwd(path, cwd):
    if not cwd:
        return path
    if path.startswith('/'):
        return path
    return os.path.join(cwd, path)


class Step(object):
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs

        self.depends = kwargs.get('depends', None)
        self.attempts = 0
        self.max_attempts = kwargs.get('max_attempts', 5)
        self.failing_step_delay = kwargs.get('failing_step_delay', 30)
        self.on_failure = kwargs.get('on_failure')

    def __str__(self):
        return 'step %s, depends on %s' % (self.name, self.depends)

    def run(self, emit, screen):
        if self.attempts > 0:
            emit.emit('... not our first attempt, sleeping for %s seconds'
                      % self.failing_step_delay)
            time.sleep(self.failing_step_delay)

        self.attempts += 1

        if self.attempts > self.max_attempts:
            emit.emit('... repeatedly failed step, giving up')
            sys.exit(1)

        emit.emit('Running %s' % self)
        emit.emit('   with kwargs: %s' % self.kwargs)
        emit.emit('\n')
        return self._run(emit, screen)


class KwargsStep(Step):
    def __init__(self, name, r, kwarg_updates, **kwargs):
        super(KwargsStep, self).__init__(name, **kwargs)
        self.r = r
        self.kwarg_updates = kwarg_updates

    def run(self, emit, screen):
        utils.recursive_dictionary_update(self.r.kwargs, self.kwarg_updates)
        emit.emit(json.dumps(self.r.kwargs, indent=4, sort_keys=True))
        return True


class SimpleCommandStep(Step):
    def __init__(self, name, command, **kwargs):
        super(SimpleCommandStep, self).__init__(name, **kwargs)
        self.command = command
        self.cwd = kwargs.get('cwd')
        self.trace_processes = kwargs.get('trace_processes', False)

        self.env = os.environ
        self.env.update(kwargs.get('env'))

        self.acceptable_exit_codes = kwargs.get(
            'acceptable_exit_codes', [0])

    def _output_analysis(self, d):
        pass

    def _run(self, emit, screen):
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
                d = os.read(f.fileno(), 10000)
                self._output_analysis(d)
                emit.emit(d)

            seen = []
            for child in proc.children(recursive=True):
                try:
                    seen.append(child.pid)
                    if child.pid not in procs:
                        procs[child.pid] = ' '.join(child.cmdline())
                        if self.trace_processes:
                            emit.emit('*** process started *** %d -> %s'
                                      % (child.pid, procs[child.pid]))
                except psutil.NoSuchProcess:
                    pass

            ended = []
            for pid in procs:
                if pid not in seen:
                    if self.trace_processes:
                        emit.emit('*** process ended *** %d -> %s'
                              % (pid, procs.get(child.pid, '???')))
                    ended.append(pid)

            for pid in ended:
                del procs[pid]

        emit.emit('... process complete')
        returncode = obj.returncode
        emit.emit('... exit code %d' % returncode)
        return returncode in self.acceptable_exit_codes


EXECUTION_RE = re.compile('^\[Executing "(.*)" playbook\]$')
RUN_TIME_RE = re.compile('^Run Time = ([0-9]+) seconds$')


class AnsibleTimingSimpleCommandStep(SimpleCommandStep):
    def __init__(self, name, command, timings_path, **kwargs):
        super(AnsibleTimingSimpleCommandStep, self).__init__(
            name, command, **kwargs)
        self.playbook = None

        self.timings = []
        self.timings_path = timings_path
        if os.path.exists(self.timings_path):
            with open(self.timings_path, 'r') as f:
                self.timings = json.loads(f.read())

    def _output_analysis(self, d):
        for line in d.split('\n'):
            m = EXECUTION_RE.match(line)
            if m:
                self.playbook = m.group(1)

            m = RUN_TIME_RE.match(line)
            if m and self.playbook:
                self.timings.append((self.playbook, m.group(1)))

    def _run(self, emit, screen):
        res = super(AnsibleTimingSimpleCommandStep, self)._run(emit, screen)

        with open(self.timings_path, 'w') as f:
            f.write(json.dumps(self.timings, indent=4))

        return res


class PatchStep(SimpleCommandStep):
    def __init__(self, name, **kwargs):
        self.local_kwargs = copy.copy(kwargs)
        self.local_kwargs['cwd'] = __file__.replace('/ostrich/steps.py', '')
        self.local_kwargs['acceptable_exit_codes'] = [0, 1]

        self.archive_path = os.path.expanduser('~/.ostrich')

        self.files = []
        with open(os.path.join(self.local_kwargs['cwd'],
                               'patches/%s' % name)) as f:
            for line in f.readlines():
                if line.startswith('--- '):
                     self.files.append(line.split()[1])

        super(PatchStep, self).__init__(
            name,
            'patch -d / -p 1 --verbose < patches/%s' % name,
            **self.local_kwargs)

    def _archive_files(self, stage):
        for f in self.files:
            arc_path = os.path.join(self.archive_path,
                                    '%s-%s-%s'
                                    % (self.name, f.replace('/', '_'), stage))
            if not os.path.exists(arc_path):
                shutil.copyfile(f, arc_path)

    def _run(self, emit, screen):
        self._archive_files('before')
        res = super(PatchStep, self)._run(emit, screen)
        self._archive_files('after')
        return res


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

        return 'Changed %d files' % changes


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


class FileCreateStep(Step):
    def __init__(self, name, path, text, **kwargs):
        super(FileCreateStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.text = text

    def _run(self, emit, screen):
        if os.path.exists(self.path):
            emit.emit('%s exists' % self.path)
            return False

        with open(self.path, 'w') as f:
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
            print key
            sub = sub[key]

        sub.append(self.data)

        emit.emit('YAML after changes:')
        emit.emit(yaml.dump(y))

        with open(self.path, 'w') as f:
            f.write(yaml.dump(y, default_flow_style=False))

        return True


class YamlUpdateElementStep(Step):
    def __init__(self, name, path, target_element_path, target_key, data,
                 **kwargs):
        super(YamlUpdateElementStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.target_element_path = target_element_path
        self.target_key = target_key
        self.data = data

    def _run(self, emit, screen):
        with open(self.path) as f:
            y = yaml.load(f.read())

        sub = y

        for key in self.target_element_path:
            sub = sub[key]

        sub[self.target_key] = self.data

        emit.emit('YAML after changes:')
        emit.emit(yaml.dump(y))

        with open(self.path, 'w') as f:
            f.write(yaml.dump(y, default_flow_style=False))

        return True


class YamlDeleteElementStep(Step):
    def __init__(self, name, path, target_element_path, index, **kwargs):
        super(YamlDeleteElementStep, self).__init__(name, **kwargs)
        self.path = _handle_path_in_cwd(path, kwargs.get('cwd'))
        self.target_element_path = target_element_path
        self.index = index

    def _run(self, emit, screen):
        with open(self.path) as f:
            y = yaml.load(f.read())

        sub = y

        for key in self.target_element_path:
            sub = sub[key]

        del sub[self.index]

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
