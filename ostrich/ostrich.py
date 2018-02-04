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
#
# An OpenStack ansible install runner
#

import argparse
import copy
import curses
import importlib
import os
import re
import sys

import runner
import stage_loader
import steps
import utils


ARGS = None


def deploy(screen):
    global ARGS

    if not ARGS.no_curses:
        screen.nodelay(False)

    r = runner.Runner(screen)

    # Generic stage lookup tool. This allows deployers to add stages without
    # re-coding the underlying engine, and for new stages to be added without
    # a lot of plumbing.
    for stage_pyname in stage_loader.discover_stages():
        name = stage_pyname.replace('.py', '')
        if name.startswith('stage'):
            module = importlib.import_module('ostrich.stages.%s' % name)
            r.load_dependancy_chain(module.get_steps(r))
            r.resolve_steps(use_curses=(not ARGS.no_curses))


def main():
    global ARGS

    parser = argparse.ArgumentParser()
    parser.add_argument('--no-screen', dest='no_screen',
                        default=False, action='store_true',
                        help='Do not force me to use screen or tmux')
    parser.add_argument('--no-curses', dest='no_curses',
                        default=False, action='store_true',
                        help='Do not use curses for the UI')
    ARGS, extras = parser.parse_known_args()

    # We really like persistent sessions
    if not ARGS.no_screen:
        if ('TMUX' not in os.environ) and ('STY' not in os.environ):
            sys.exit('Only run ostrich in a screen or tmux session please')

    if ARGS.no_curses:
        deploy(None)
    else:
        curses.wrapper(deploy)
