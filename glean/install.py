# Copyright (c) 2015 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import logging
import os
import pkg_resources
import subprocess
import sys

log = logging.getLogger("glean-install")


def _find_scripts_dir():
    p = pkg_resources.resource_filename(__name__, "init")
    return p


def install(source_file, target_file, mode='0755', replacements=dict()):
    """Install given SOURCE_FILE to TARGET_FILE with given MODE

    REPLACEMENTS is a dictionary where each KEY will result in the
    template "%%KEY%%" being replaced with its VALUE in TARGET_FILE
    (this is just a sed -i wrapper)

    :param source_file: file to be installed
    :param target_file: location file is being installed to
    :param mode: mode of file being installed
    :param replacements: dict of key/value replacements to the file
    """

    log.info("Installing %s -> %s" % (source_file, target_file))

    script_dir = _find_scripts_dir()

    cmd = ('install -D -g root -o root'
           ' -m {mode} {source_file} {target_file}').format(
               source_file=os.path.join(script_dir, source_file),
               target_file=target_file,
               mode=mode)
    log.info(cmd)
    ret = os.system(cmd)
    if ret != 0:
        log.error("Failed to install %s!" % source_file)
        sys.exit(ret)

    for k, v in replacements.items():
        log.info("Replacing %s -> %s in %s" % (k, v, target_file))

        cmd = 'sed -i "s|%%{k}%%|{v}|g" {target_file}'.format(
            k=k, v=v, target_file=target_file)
        log.info(cmd)
        ret = os.system(cmd)

        if ret != 0:
            log.error("Failed to substitute in %s" % target_file)
            sys.exit(ret)


def main():

    parser = argparse.ArgumentParser(
        description='Install glean init components')

    parser.add_argument("-n", "--use-nm", help="Use NetworkManager",
                        action="store_true")
    parser.add_argument("-q", "--quiet", help="Be very quiet",
                        action="store_true")
    # NOTE(dtantsur): there may be two reasons to disable the fallback:
    # 1) Remote edge deployments where DHCP (if present at all) is likely
    # incorrect and should not be used.
    # 2) Co-existing with another tool that handles DHCP differently (IPv6,
    # SLAAC) or does a more sophisticated configuration (like NetworkManager
    # or, in case of ironic-python-agent, dhcp-all-interfaces, which is **not**
    # recommended, but is done nonetheless, mostly for legacy reasons).
    parser.add_argument("--no-dhcp-fallback", action="store_true",
                        help="Do not fall back to DHCP. If this is on, "
                             "something else must configure networking or "
                             "it will be left unconfigured.")

    args = parser.parse_args()
    p = _find_scripts_dir()
    extra_args = '--no-dhcp-fallback' if args.no_dhcp_fallback else ''

    if args.quiet:
        logging.basicConfig(level=logging.ERROR)
    else:
        logging.basicConfig(level=logging.INFO)

    replacements = {
        'INTERP': sys.executable,
        'GLEAN_SCRIPTS_DIR': p,
        'EXTRA_ARGS': extra_args,
    }

    # Write the path of the currently executing interpreter into the
    # scripts dir.  This means glean shell scripts can call the
    # sibling python-glean and know that it's using the glean we
    # installed, even in a virtualenv etc.
    install('python-glean.template', os.path.join(p, 'python-glean'),
            mode='0755', replacements=replacements)

    # needs to go first because gentoo can have systemd along side openrc
    if os.path.exists('/etc/gentoo-release'):
        log.info('installing openrc services')
        install('glean.openrc', '/etc/init.d/glean', replacements=replacements)
    # Needs to check for the presence of systemd and systemctl
    # as apparently some packages may stage systemd init files
    # when systemd is not present.
    # We also cannot check the path for the init pid as the pid
    # may be wrong as install is generally executed in a chroot
    # with diskimage-builder.

    if (os.path.exists('/usr/lib/systemd/system')
            and (os.path.exists('/usr/bin/systemctl')
                 or os.path.exists('/bin/systemctl'))):

        log.info("Installing systemd services")

        log.info("Install early service")
        install(
            'glean-early.service',
            '/usr/lib/systemd/system/glean-early.service',
            mode='0644', replacements=replacements)
        subprocess.call(['systemctl', 'enable', 'glean-early.service'])
        if os.path.exists('/etc/gentoo-release'):
            install(
                'glean-networkd.service',
                '/lib/systemd/system/glean.service',
                mode='0644', replacements=replacements)
            subprocess.call(['systemctl', 'enable', 'glean.service'])
        else:
            log.info("Installing %s NetworkManager support" %
                     "with" if args.use_nm else "without")
            if args.use_nm:
                service_file = 'glean-nm.service'
            else:
                service_file = 'glean@.service'
            install(
                service_file,
                '/usr/lib/systemd/system/glean.service',
                mode='0644', replacements=replacements)
            subprocess.call(['systemctl', 'enable', 'glean.service'])
        install(
            'glean-udev.rules',
            '/etc/udev/rules.d/99-glean.rules',
            mode='0644')
        if args.use_nm:
            # NetworkManager has a "after" network-pre, and
            # glean@<interface> services have a "before".  However, if
            # udev has not yet triggered and started the glean
            # service, which it seems can be quite common in a slow
            # environment like a binary-translated nested-vm, systemd
            # may think it is fine to start NetworkManager because
            # network-pre has been reached with no blockers.  Thus we
            # override NetworkManager to wait for udev-settle, which
            # should ensure the glean service has started; which will
            # block network-pre until it finishes writing out the
            # configs.
            install(
                'nm-udev-settle.override',
                '/etc/systemd/system/NetworkManager.service.d/override.conf',
                mode='0644')

    elif os.path.exists('/etc/init'):
        log.info("Installing upstart services")
        install('glean.conf', '/etc/init/glean.conf',
                replacements=replacements)
    elif os.path.exists('/sbin/rc-update'):
        subprocess.call(['rc-update', 'add', 'glean', 'boot'])
    else:
        log.info("Installing sysv services")
        install('glean.init', '/etc/init.d/glean',
                replacements=replacements)
        os.system('update-rc.d glean defaults')


if __name__ == '__main__':
    main()
