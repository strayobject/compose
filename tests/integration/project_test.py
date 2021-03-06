from __future__ import absolute_import
from __future__ import unicode_literals

import random

import py
import pytest
from docker.errors import NotFound

from .. import mock
from ..helpers import build_config
from .testcases import DockerClientTestCase
from compose.config import config
from compose.config import ConfigurationError
from compose.config.config import V2_0
from compose.config.config import V2_1
from compose.config.types import VolumeFromSpec
from compose.config.types import VolumeSpec
from compose.const import LABEL_PROJECT
from compose.const import LABEL_SERVICE
from compose.container import Container
from compose.project import Project
from compose.project import ProjectError
from compose.service import ConvergenceStrategy
from tests.integration.testcases import v2_1_only
from tests.integration.testcases import v2_only


class ProjectTest(DockerClientTestCase):

    def test_containers(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.up()

        containers = project.containers()
        self.assertEqual(len(containers), 2)

    def test_containers_with_service_names(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.up()

        containers = project.containers(['web'])
        self.assertEqual(
            [c.name for c in containers],
            ['composetest_web_1'])

    def test_containers_with_extra_service(self):
        web = self.create_service('web')
        web_1 = web.create_container()

        db = self.create_service('db')
        db_1 = db.create_container()

        self.create_service('extra').create_container()

        project = Project('composetest', [web, db], self.client)
        self.assertEqual(
            set(project.containers(stopped=True)),
            set([web_1, db_1]),
        )

    def test_volumes_from_service(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_config({
                'data': {
                    'image': 'busybox:latest',
                    'volumes': ['/var/data'],
                },
                'db': {
                    'image': 'busybox:latest',
                    'volumes_from': ['data'],
                },
            }),
            client=self.client,
        )
        db = project.get_service('db')
        data = project.get_service('data')
        self.assertEqual(db.volumes_from, [VolumeFromSpec(data, 'rw', 'service')])

    def test_volumes_from_container(self):
        data_container = Container.create(
            self.client,
            image='busybox:latest',
            volumes=['/var/data'],
            name='composetest_data_container',
            labels={LABEL_PROJECT: 'composetest'},
        )
        project = Project.from_config(
            name='composetest',
            config_data=build_config({
                'db': {
                    'image': 'busybox:latest',
                    'volumes_from': ['composetest_data_container'],
                },
            }),
            client=self.client,
        )
        db = project.get_service('db')
        self.assertEqual(db._get_volumes_from(), [data_container.id + ':rw'])

    @v2_only()
    def test_network_mode_from_service(self):
        project = Project.from_config(
            name='composetest',
            client=self.client,
            config_data=build_config({
                'version': V2_0,
                'services': {
                    'net': {
                        'image': 'busybox:latest',
                        'command': ["top"]
                    },
                    'web': {
                        'image': 'busybox:latest',
                        'network_mode': 'service:net',
                        'command': ["top"]
                    },
                },
            }),
        )

        project.up()

        web = project.get_service('web')
        net = project.get_service('net')
        self.assertEqual(web.network_mode.mode, 'container:' + net.containers()[0].id)

    @v2_only()
    def test_network_mode_from_container(self):
        def get_project():
            return Project.from_config(
                name='composetest',
                config_data=build_config({
                    'version': V2_0,
                    'services': {
                        'web': {
                            'image': 'busybox:latest',
                            'network_mode': 'container:composetest_net_container'
                        },
                    },
                }),
                client=self.client,
            )

        with pytest.raises(ConfigurationError) as excinfo:
            get_project()

        assert "container 'composetest_net_container' which does not exist" in excinfo.exconly()

        net_container = Container.create(
            self.client,
            image='busybox:latest',
            name='composetest_net_container',
            command='top',
            labels={LABEL_PROJECT: 'composetest'},
        )
        net_container.start()

        project = get_project()
        project.up()

        web = project.get_service('web')
        self.assertEqual(web.network_mode.mode, 'container:' + net_container.id)

    def test_net_from_service_v1(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_config({
                'net': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'web': {
                    'image': 'busybox:latest',
                    'net': 'container:net',
                    'command': ["top"]
                },
            }),
            client=self.client,
        )

        project.up()

        web = project.get_service('web')
        net = project.get_service('net')
        self.assertEqual(web.network_mode.mode, 'container:' + net.containers()[0].id)

    def test_net_from_container_v1(self):
        def get_project():
            return Project.from_config(
                name='composetest',
                config_data=build_config({
                    'web': {
                        'image': 'busybox:latest',
                        'net': 'container:composetest_net_container'
                    },
                }),
                client=self.client,
            )

        with pytest.raises(ConfigurationError) as excinfo:
            get_project()

        assert "container 'composetest_net_container' which does not exist" in excinfo.exconly()

        net_container = Container.create(
            self.client,
            image='busybox:latest',
            name='composetest_net_container',
            command='top',
            labels={LABEL_PROJECT: 'composetest'},
        )
        net_container.start()

        project = get_project()
        project.up()

        web = project.get_service('web')
        self.assertEqual(web.network_mode.mode, 'container:' + net_container.id)

    def test_start_pause_unpause_stop_kill_remove(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.start()

        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(db.containers()), 0)

        web_container_1 = web.create_container()
        web_container_2 = web.create_container()
        db_container = db.create_container()

        project.start(service_names=['web'])
        self.assertEqual(
            set(c.name for c in project.containers()),
            set([web_container_1.name, web_container_2.name]))

        project.start()
        self.assertEqual(
            set(c.name for c in project.containers()),
            set([web_container_1.name, web_container_2.name, db_container.name]))

        project.pause(service_names=['web'])
        self.assertEqual(
            set([c.name for c in project.containers() if c.is_paused]),
            set([web_container_1.name, web_container_2.name]))

        project.pause()
        self.assertEqual(
            set([c.name for c in project.containers() if c.is_paused]),
            set([web_container_1.name, web_container_2.name, db_container.name]))

        project.unpause(service_names=['db'])
        self.assertEqual(len([c.name for c in project.containers() if c.is_paused]), 2)

        project.unpause()
        self.assertEqual(len([c.name for c in project.containers() if c.is_paused]), 0)

        project.stop(service_names=['web'], timeout=1)
        self.assertEqual(set(c.name for c in project.containers()), set([db_container.name]))

        project.kill(service_names=['db'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 3)

        project.remove_stopped(service_names=['web'])
        self.assertEqual(len(project.containers(stopped=True)), 1)

        project.remove_stopped()
        self.assertEqual(len(project.containers(stopped=True)), 0)

    def test_create(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)

        project.create(['db'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers(stopped=True)), 0)

    def test_create_twice(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)

        project.create(['db', 'web'])
        project.create(['db', 'web'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(web.containers(stopped=True)), 1)

    def test_create_with_links(self):
        db = self.create_service('db')
        web = self.create_service('web', links=[(db, 'db')])
        project = Project('composetest', [db, web], self.client)

        project.create(['web'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(web.containers(stopped=True)), 1)

    def test_create_strategy_always(self):
        db = self.create_service('db')
        project = Project('composetest', [db], self.client)
        project.create(['db'])
        old_id = project.containers(stopped=True)[0].id

        project.create(['db'], strategy=ConvergenceStrategy.always)
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)

        db_container = project.containers(stopped=True)[0]
        self.assertNotEqual(db_container.id, old_id)

    def test_create_strategy_never(self):
        db = self.create_service('db')
        project = Project('composetest', [db], self.client)
        project.create(['db'])
        old_id = project.containers(stopped=True)[0].id

        project.create(['db'], strategy=ConvergenceStrategy.never)
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)

        db_container = project.containers(stopped=True)[0]
        self.assertEqual(db_container.id, old_id)

    def test_project_up(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(web.containers()), 0)

    def test_project_up_starts_uncreated_services(self):
        db = self.create_service('db')
        web = self.create_service('web', links=[(db, 'db')])
        project = Project('composetest', [db, web], self.client)
        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)

        project.up()
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(web.containers()), 1)

    def test_recreate_preserves_volumes(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/etc')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        old_db_id = project.containers()[0].id
        db_volume_path = project.containers()[0].get('Volumes./etc')

        project.up(strategy=ConvergenceStrategy.always)
        self.assertEqual(len(project.containers()), 2)

        db_container = [c for c in project.containers() if 'db' in c.name][0]
        self.assertNotEqual(db_container.id, old_db_id)
        self.assertEqual(db_container.get('Volumes./etc'), db_volume_path)

    def test_project_up_with_no_recreate_running(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        old_db_id = project.containers()[0].id
        container, = project.containers()
        db_volume_path = container.get_mount('/var/db')['Source']

        project.up(strategy=ConvergenceStrategy.never)
        self.assertEqual(len(project.containers()), 2)

        db_container = [c for c in project.containers() if 'db' in c.name][0]
        self.assertEqual(db_container.id, old_db_id)
        self.assertEqual(
            db_container.get_mount('/var/db')['Source'],
            db_volume_path)

    def test_project_up_with_no_recreate_stopped(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        project.kill()

        old_containers = project.containers(stopped=True)

        self.assertEqual(len(old_containers), 1)
        old_container, = old_containers
        old_db_id = old_container.id
        db_volume_path = old_container.get_mount('/var/db')['Source']

        project.up(strategy=ConvergenceStrategy.never)

        new_containers = project.containers(stopped=True)
        self.assertEqual(len(new_containers), 2)
        self.assertEqual([c.is_running for c in new_containers], [True, True])

        db_container = [c for c in new_containers if 'db' in c.name][0]
        self.assertEqual(db_container.id, old_db_id)
        self.assertEqual(
            db_container.get_mount('/var/db')['Source'],
            db_volume_path)

    def test_project_up_without_all_services(self):
        console = self.create_service('console')
        db = self.create_service('db')
        project = Project('composetest', [console, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up()
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(console.containers()), 1)

    def test_project_up_starts_links(self):
        console = self.create_service('console')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        web = self.create_service('web', links=[(db, 'db')])

        project = Project('composetest', [web, db, console], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['web'])
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(web.containers()), 1)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(console.containers()), 0)

    def test_project_up_starts_depends(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_config({
                'console': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                },
                'data': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'db': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'volumes_from': ['data'],
                },
                'web': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'links': ['db'],
                },
            }),
            client=self.client,
        )
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['web'])
        self.assertEqual(len(project.containers()), 3)
        self.assertEqual(len(project.get_service('web').containers()), 1)
        self.assertEqual(len(project.get_service('db').containers()), 1)
        self.assertEqual(len(project.get_service('data').containers()), 1)
        self.assertEqual(len(project.get_service('console').containers()), 0)

    def test_project_up_with_no_deps(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_config({
                'console': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                },
                'data': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'db': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'volumes_from': ['data'],
                },
                'web': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'links': ['db'],
                },
            }),
            client=self.client,
        )
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'], start_deps=False)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(project.get_service('web').containers()), 0)
        self.assertEqual(len(project.get_service('db').containers()), 1)
        self.assertEqual(len(project.get_service('data').containers()), 0)
        self.assertEqual(len(project.get_service('data').containers(stopped=True)), 1)
        self.assertEqual(len(project.get_service('console').containers()), 0)

    def test_unscale_after_restart(self):
        web = self.create_service('web')
        project = Project('composetest', [web], self.client)

        project.start()

        service = project.get_service('web')
        service.scale(1)
        self.assertEqual(len(service.containers()), 1)
        service.scale(3)
        self.assertEqual(len(service.containers()), 3)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 3)
        service.scale(1)
        self.assertEqual(len(service.containers()), 1)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 1)
        # does scale=0 ,makes any sense? after recreating at least 1 container is running
        service.scale(0)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 1)

    @v2_only()
    def test_project_up_networks(self):
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top',
                'networks': {
                    'foo': None,
                    'bar': None,
                    'baz': {'aliases': ['extra']},
                },
            }],
            volumes={},
            networks={
                'foo': {'driver': 'bridge'},
                'bar': {'driver': None},
                'baz': {},
            },
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )
        project.up()

        containers = project.containers()
        assert len(containers) == 1
        container, = containers

        for net_name in ['foo', 'bar', 'baz']:
            full_net_name = 'composetest_{}'.format(net_name)
            network_data = self.client.inspect_network(full_net_name)
            assert network_data['Name'] == full_net_name

        aliases_key = 'NetworkSettings.Networks.{net}.Aliases'
        assert 'web' in container.get(aliases_key.format(net='composetest_foo'))
        assert 'web' in container.get(aliases_key.format(net='composetest_baz'))
        assert 'extra' in container.get(aliases_key.format(net='composetest_baz'))

        foo_data = self.client.inspect_network('composetest_foo')
        assert foo_data['Driver'] == 'bridge'

    @v2_only()
    def test_up_with_ipam_config(self):
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'networks': {'front': None},
            }],
            volumes={},
            networks={
                'front': {
                    'driver': 'bridge',
                    'driver_opts': {
                        "com.docker.network.bridge.enable_icc": "false",
                    },
                    'ipam': {
                        'driver': 'default',
                        'config': [{
                            "subnet": "172.28.0.0/16",
                            "ip_range": "172.28.5.0/24",
                            "gateway": "172.28.5.254",
                            "aux_addresses": {
                                "a": "172.28.1.5",
                                "b": "172.28.1.6",
                                "c": "172.28.1.7",
                            },
                        }],
                    },
                },
            },
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )
        project.up()

        network = self.client.networks(names=['composetest_front'])[0]

        assert network['Options'] == {
            "com.docker.network.bridge.enable_icc": "false"
        }

        assert network['IPAM'] == {
            'Driver': 'default',
            'Options': None,
            'Config': [{
                'Subnet': "172.28.0.0/16",
                'IPRange': "172.28.5.0/24",
                'Gateway': "172.28.5.254",
                'AuxiliaryAddresses': {
                    'a': '172.28.1.5',
                    'b': '172.28.1.6',
                    'c': '172.28.1.7',
                },
            }],
        }

    @v2_only()
    def test_up_with_network_static_addresses(self):
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top',
                'networks': {
                    'static_test': {
                        'ipv4_address': '172.16.100.100',
                        'ipv6_address': 'fe80::1001:102'
                    }
                },
            }],
            volumes={},
            networks={
                'static_test': {
                    'driver': 'bridge',
                    'driver_opts': {
                        "com.docker.network.enable_ipv6": "true",
                    },
                    'ipam': {
                        'driver': 'default',
                        'config': [
                            {"subnet": "172.16.100.0/24",
                             "gateway": "172.16.100.1"},
                            {"subnet": "fe80::/64",
                             "gateway": "fe80::1001:1"}
                        ]
                    }
                }
            }
        )
        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )
        project.up(detached=True)

        network = self.client.networks(names=['static_test'])[0]
        service_container = project.get_service('web').containers()[0]

        assert network['Options'] == {
            "com.docker.network.enable_ipv6": "true"
        }

        IPAMConfig = (service_container.inspect().get('NetworkSettings', {}).
                      get('Networks', {}).get('composetest_static_test', {}).
                      get('IPAMConfig', {}))
        assert IPAMConfig.get('IPv4Address') == '172.16.100.100'
        assert IPAMConfig.get('IPv6Address') == 'fe80::1001:102'

    @v2_1_only()
    def test_up_with_enable_ipv6(self):
        self.require_api_version('1.23')
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top',
                'networks': {
                    'static_test': {
                        'ipv6_address': 'fe80::1001:102'
                    }
                },
            }],
            volumes={},
            networks={
                'static_test': {
                    'driver': 'bridge',
                    'enable_ipv6': True,
                    'ipam': {
                        'driver': 'default',
                        'config': [
                            {"subnet": "fe80::/64",
                             "gateway": "fe80::1001:1"}
                        ]
                    }
                }
            }
        )
        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )
        project.up(detached=True)
        network = self.client.networks(names=['static_test'])[0]
        service_container = project.get_service('web').containers()[0]

        assert network['EnableIPv6'] is True
        ipam_config = (service_container.inspect().get('NetworkSettings', {}).
                       get('Networks', {}).get('composetest_static_test', {}).
                       get('IPAMConfig', {}))
        assert ipam_config.get('IPv6Address') == 'fe80::1001:102'

    @v2_only()
    def test_up_with_network_static_addresses_missing_subnet(self):
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'networks': {
                    'static_test': {
                        'ipv4_address': '172.16.100.100',
                        'ipv6_address': 'fe80::1001:101'
                    }
                },
            }],
            volumes={},
            networks={
                'static_test': {
                    'driver': 'bridge',
                    'driver_opts': {
                        "com.docker.network.enable_ipv6": "true",
                    },
                    'ipam': {
                        'driver': 'default',
                    },
                },
            },
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )

        with self.assertRaises(ProjectError):
            project.up()

    @v2_1_only()
    def test_up_with_network_link_local_ips(self):
        config_data = config.Config(
            version=V2_1,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'networks': {
                    'linklocaltest': {
                        'link_local_ips': ['169.254.8.8']
                    }
                }
            }],
            volumes={},
            networks={
                'linklocaltest': {'driver': 'bridge'}
            }
        )
        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data
        )
        project.up()

        service_container = project.get_service('web').containers()[0]
        ipam_config = service_container.inspect().get(
            'NetworkSettings', {}
        ).get(
            'Networks', {}
        ).get(
            'composetest_linklocaltest', {}
        ).get('IPAMConfig', {})
        assert 'LinkLocalIPs' in ipam_config
        assert ipam_config['LinkLocalIPs'] == ['169.254.8.8']

    @v2_1_only()
    def test_up_with_isolation(self):
        self.require_api_version('1.24')
        config_data = config.Config(
            version=V2_1,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'isolation': 'default'
            }],
            volumes={},
            networks={}
        )
        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data
        )
        project.up()
        service_container = project.get_service('web').containers()[0]
        assert service_container.inspect()['HostConfig']['Isolation'] == 'default'

    @v2_1_only()
    def test_up_with_invalid_isolation(self):
        self.require_api_version('1.24')
        config_data = config.Config(
            version=V2_1,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'isolation': 'foobar'
            }],
            volumes={},
            networks={}
        )
        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data
        )
        with self.assertRaises(ProjectError):
            project.up()

    @v2_only()
    def test_project_up_with_network_internal(self):
        self.require_api_version('1.23')
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'networks': {'internal': None},
            }],
            volumes={},
            networks={
                'internal': {'driver': 'bridge', 'internal': True},
            },
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )
        project.up()

        network = self.client.networks(names=['composetest_internal'])[0]

        assert network['Internal'] is True

    @v2_1_only()
    def test_project_up_with_network_label(self):
        self.require_api_version('1.23')

        network_name = 'network_with_label'

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'networks': {network_name: None}
            }],
            volumes={},
            networks={
                network_name: {'labels': {'label_key': 'label_val'}}
            }
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data
        )

        project.up()

        networks = [
            n for n in self.client.networks()
            if n['Name'].startswith('composetest_')
        ]

        assert [n['Name'] for n in networks] == ['composetest_{}'.format(network_name)]

        assert networks[0]['Labels'] == {'label_key': 'label_val'}

    @v2_only()
    def test_project_up_volumes(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {'driver': 'local'}},
            networks={},
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.up()
        self.assertEqual(len(project.containers()), 1)

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    @v2_1_only()
    def test_project_up_with_volume_labels(self):
        self.require_api_version('1.23')

        volume_name = 'volume_with_label'

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'volumes': [VolumeSpec.parse('{}:/data'.format(volume_name))]
            }],
            volumes={
                volume_name: {
                    'labels': {
                        'label_key': 'label_val'
                    }
                }
            },
            networks={},
        )

        project = Project.from_config(
            client=self.client,
            name='composetest',
            config_data=config_data,
        )

        project.up()

        volumes = [
            v for v in self.client.volumes().get('Volumes', [])
            if v['Name'].startswith('composetest_')
        ]

        assert [v['Name'] for v in volumes] == ['composetest_{}'.format(volume_name)]

        assert volumes[0]['Labels'] == {'label_key': 'label_val'}

    @v2_only()
    def test_project_up_logging_with_multiple_files(self):
        base_file = config.ConfigFile(
            'base.yml',
            {
                'version': V2_0,
                'services': {
                    'simple': {'image': 'busybox:latest', 'command': 'top'},
                    'another': {
                        'image': 'busybox:latest',
                        'command': 'top',
                        'logging': {
                            'driver': "json-file",
                            'options': {
                                'max-size': "10m"
                            }
                        }
                    }
                }

            })
        override_file = config.ConfigFile(
            'override.yml',
            {
                'version': V2_0,
                'services': {
                    'another': {
                        'logging': {
                            'driver': "none"
                        }
                    }
                }

            })
        details = config.ConfigDetails('.', [base_file, override_file])

        tmpdir = py.test.ensuretemp('logging_test')
        self.addCleanup(tmpdir.remove)
        with tmpdir.as_cwd():
            config_data = config.load(details)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        project.up()
        containers = project.containers()
        self.assertEqual(len(containers), 2)

        another = project.get_service('another').containers()[0]
        log_config = another.get('HostConfig.LogConfig')
        self.assertTrue(log_config)
        self.assertEqual(log_config.get('Type'), 'none')

    @v2_only()
    def test_project_up_port_mappings_with_multiple_files(self):
        base_file = config.ConfigFile(
            'base.yml',
            {
                'version': V2_0,
                'services': {
                    'simple': {
                        'image': 'busybox:latest',
                        'command': 'top',
                        'ports': ['1234:1234']
                    },
                },

            })
        override_file = config.ConfigFile(
            'override.yml',
            {
                'version': V2_0,
                'services': {
                    'simple': {
                        'ports': ['1234:1234']
                    }
                }

            })
        details = config.ConfigDetails('.', [base_file, override_file])

        config_data = config.load(details)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        project.up()
        containers = project.containers()
        self.assertEqual(len(containers), 1)

    @v2_only()
    def test_initialize_volumes(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {}},
            networks={},
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.volumes.initialize()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    @v2_only()
    def test_project_up_implicit_volume_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {}},
            networks={},
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.up()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    @v2_only()
    def test_initialize_volumes_invalid_volume_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {'driver': 'foobar'}},
            networks={},
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        with self.assertRaises(config.ConfigurationError):
            project.volumes.initialize()

    @v2_only()
    def test_initialize_volumes_updated_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {'driver': 'local'}},
            networks={},
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.volumes.initialize()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

        config_data = config_data._replace(
            volumes={vol_name: {'driver': 'smb'}}
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data,
            client=self.client
        )
        with self.assertRaises(config.ConfigurationError) as e:
            project.volumes.initialize()
        assert 'Configuration for volume {0} specifies driver smb'.format(
            vol_name
        ) in str(e.exception)

    @v2_only()
    def test_initialize_volumes_updated_blank_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={vol_name: {'driver': 'local'}},
            networks={},
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.volumes.initialize()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

        config_data = config_data._replace(
            volumes={vol_name: {}}
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data,
            client=self.client
        )
        project.volumes.initialize()
        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    @v2_only()
    def test_initialize_volumes_external_volumes(self):
        # Use composetest_ prefix so it gets garbage-collected in tearDown()
        vol_name = 'composetest_{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        self.client.create_volume(vol_name)
        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={
                vol_name: {'external': True, 'external_name': vol_name}
            },
            networks=None,
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.volumes.initialize()

        with self.assertRaises(NotFound):
            self.client.inspect_volume(full_vol_name)

    @v2_only()
    def test_initialize_volumes_inexistent_external_volume(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))

        config_data = config.Config(
            version=V2_0,
            services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }],
            volumes={
                vol_name: {'external': True, 'external_name': vol_name}
            },
            networks=None,
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        with self.assertRaises(config.ConfigurationError) as e:
            project.volumes.initialize()
        assert 'Volume {0} declared as external'.format(
            vol_name
        ) in str(e.exception)

    @v2_only()
    def test_project_up_named_volumes_in_binds(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)

        base_file = config.ConfigFile(
            'base.yml',
            {
                'version': V2_0,
                'services': {
                    'simple': {
                        'image': 'busybox:latest',
                        'command': 'top',
                        'volumes': ['{0}:/data'.format(vol_name)]
                    },
                },
                'volumes': {
                    vol_name: {'driver': 'local'}
                }

            })
        config_details = config.ConfigDetails('.', [base_file])
        config_data = config.load(config_details)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        service = project.services[0]
        self.assertEqual(service.name, 'simple')
        volumes = service.options.get('volumes')
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0].external, full_vol_name)
        project.up()
        engine_volumes = self.client.volumes()['Volumes']
        container = service.get_container()
        assert [mount['Name'] for mount in container.get('Mounts')] == [full_vol_name]
        assert next((v for v in engine_volumes if v['Name'] == vol_name), None) is None

    def test_project_up_orphans(self):
        config_dict = {
            'service1': {
                'image': 'busybox:latest',
                'command': 'top',
            }
        }

        config_data = build_config(config_dict)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        project.up()
        config_dict['service2'] = config_dict['service1']
        del config_dict['service1']

        config_data = build_config(config_dict)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        with mock.patch('compose.project.log') as mock_log:
            project.up()

        mock_log.warning.assert_called_once_with(mock.ANY)

        assert len([
            ctnr for ctnr in project._labeled_containers()
            if ctnr.labels.get(LABEL_SERVICE) == 'service1'
        ]) == 1

        project.up(remove_orphans=True)

        assert len([
            ctnr for ctnr in project._labeled_containers()
            if ctnr.labels.get(LABEL_SERVICE) == 'service1'
        ]) == 0
