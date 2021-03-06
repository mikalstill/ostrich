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
    """Configure proxies."""

    nextsteps = []

    if r.complete['osa-branch'] == 'stable/mitaka':
        if r.complete['http-proxy'] and r.complete['http-proxy'] != 'none':
            local_servers = 'localhost,127.0.0.1'
            if r.complete['local-cache'] != 'none':
                local_servers += ',%s' % r.complete['local-cache']

            r.kwargs['env'].update({'http_proxy': r.complete['http-proxy'],
                                    'https_proxy': r.complete['http-proxy'],
                                    'no_proxy': local_servers})

            # This entry will only last until it is clobbered by ansible
            nextsteps.append(
                steps.FileAppendStep(
                    'proxy-environment',
                    '/etc/environment',
                    (('\n\nexport http_proxy="%(proxy)s"\n'
                      'export HTTP_PROXY="%(proxy)s"\n'
                      'export https_proxy="%(proxy)s"\n'
                      'export HTTPS_PROXY="%(proxy)s"\n'
                      'export ftp_proxy="%(proxy)s"\n'
                      'export FTP_PROXY="%(proxy)s"\n'
                      'export no_proxy=%(local)s\n'
                      'export NO_PROXY=%(local)sn')
                     % {'proxy': r.complete['http-proxy'],
                        'local': local_servers}),
                    **r.kwargs)
                )

    replacements = [
        ('(http|https|git)://github.com',
         r.complete['git-mirror-github']),
        ('(http|https|git)://git.openstack.org',
         r.complete['git-mirror-openstack']),
        ]

    if r.complete['local-cache'] != 'none':
        replacements.append(
            ('https://rpc-repo.rackspace.com',
             'http://%s/rpc-repo.rackspace.com' % r.complete['local-cache'])
            )

    nextsteps.append(
        steps.BulkRegexpEditorStep(
            'bulk-edit-osa',
            '/opt/openstack-ansible',
            '.*\.(ini|yml|sh)$',
            replacements,
            **r.kwargs)
        )

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
