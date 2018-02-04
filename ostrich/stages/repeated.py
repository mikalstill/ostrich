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
from ostrich import utils


def configure_proxies(r, phase):
    """Tweaks to configuration before we run the playbooks."""

    nextsteps = []

    # We also need to re-write git repos in a large number of roles
    replacements = [
        ('(http|https|git)://github.com',
         r.complete['git-mirror-github']),
        ('(http|https|git)://git.openstack.org',
         r.complete['git-mirror-openstack']),
        ('https://mirror.rackspace.com',
         'http://mirror.rackspace.com'),
        (' +checksum:.*', ''),
        ]

    if r.complete['local-cache'] != 'none':
        replacements.append(
            ('https://rpc-repo.rackspace.com',
             'http://%s/rpc-repo.rackspace.com' % r.complete['local-cache'])
            )
        replacements.append(
            ('https://bootstrap.pypa.io/get-pip.py',
             'http://%s/pip/get-pip.py' % r.complete['local-cache'])
            )

    nextsteps.append(
        steps.BulkRegexpEditorStep(
            'bulk-edit-roles-%s' % phase,
            '/etc/ansible',
            '.*\.(ini|yml)$',
            replacements,
            **r.kwargs))

    nextsteps.append(
        steps.BulkRegexpEditorStep(
            'unapply-git-mirrors-for-cgit',
            '/opt/openstack-ansible',
            '.*\.(ini|yml|sh)$',
            [
                ('%s/cgit' % r.complete['git-mirror-openstack'],
                 'https://git.openstack.org/cgit')
            ],
            **r.kwargs)
        )

    return nextsteps
