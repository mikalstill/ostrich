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

from ostrich import steps


def get_steps(r):
    """Bootstrap ansible and AIO."""

    nextsteps = []

    nextsteps.append(
        steps.SimpleCommandStep(
            'bootstrap-ansible',
            './scripts/bootstrap-ansible.sh',
            **r.kwargs)
        )
    nextsteps.append(
        steps.SimpleCommandStep(
            'bootstrap-aio',
            './scripts/bootstrap-aio.sh',
            **r.kwargs)
        )

    return nextsteps
