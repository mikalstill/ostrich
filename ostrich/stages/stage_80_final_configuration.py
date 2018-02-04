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

from ostrich.stages import repeated
from ostrich import steps
from ostrich import utils


def get_steps(r):
    """Final tweaks to configuration before we run the playbooks."""

    nextsteps = repeated.configure_proxies(r, 'pass-2')

    # Release specific steps: Mitaka
    if r.complete['osa-branch'] == 'stable/mitaka' and utils.is_ironic(r):
        nextsteps.append(
            steps.CopyFileStep(
                'enable-ironic-environment-mitaka',
                'etc/openstack_deploy/env.d/ironic.yml',
                '/etc/openstack_deploy/env.d/ironic.yml',
                **r.kwargs)
            )

    if utils.is_ironic(r):
        nextsteps.append(steps.PatchStep('ironic-tftp-address', **r.kwargs))

        if r.complete['osa-branch'] == 'stable/mitaka':
            nextsteps.append(steps.PatchStep('ironic-pxe-options', **r.kwargs))
        else:
            nextsteps.append(steps.PatchStep('ironic-pxe-options-newton',
                                             **r.kwargs))

    return nextsteps
