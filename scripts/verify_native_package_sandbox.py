#!/usr/bin/env python3
"""Offline native probes: individual ELF mounts, separate UID, no host user state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
import select
import time
import tempfile


def snapshot(roots):
    """Hash bounded configuration/plugin trees; callers exclude active session stores."""
    result = {}
    for root in roots:
        root = Path(root)
        digest = hashlib.sha256()
        count = 0
        if root.is_symlink():
            raise ValueError('Snapshot roots must not be links')
        for parent, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(dirs + files):
                path = Path(parent) / name
                info = path.lstat()
                digest.update(str(path.relative_to(root)).encode() + str(info.st_mode).encode())
                if path.is_symlink():
                    digest.update(os.readlink(path).encode())
                elif path.is_file():
                    with path.open('rb') as stream:
                        while chunk := stream.read(1024 * 1024):
                            digest.update(chunk)
                count += 1
        result[str(root)] = {'exists': root.exists(), 'entries': count, 'sha256': digest.hexdigest()}
    return result


class SandboxRunner:
    """Only a disposable test tree is writable. Never pass a real harness home."""
    def __init__(self, image, binaries, root):
        self.image = subprocess.check_output(
            ['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        self.root = Path(root).resolve(strict=True)
        if not self.root.name.startswith('skill-manager-native-test-') or not self.root.is_relative_to('/tmp'):
            raise ValueError('A newly created task-owned temporary test root is required')
        self.binaries = {}
        for harness, value in binaries.items():
            path = Path(value)
            with path.open('rb') as stream:
                magic = stream.read(4)
            if path.is_symlink() or magic != b'\x7fELF':
                raise ValueError('Use a raw ELF, never a user wrapper')
            self.binaries[harness] = path

    def release(self):
        # Let the host test owner clean up files created by the distinct native UID.
        self.root.chmod(0o777)
        subprocess.run(['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                        '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                        '--mount', f'type=bind,src={self.root},dst={self.root}',
                        '--entrypoint', '/bin/chmod', self.image, '-R', 'a+rwX', str(self.root)],
                       capture_output=True, timeout=20)

    def __call__(self, argv, requests=None):
        harness = argv[0]
        root = str(self.root)
        env = {
            'HOME': root + '/home', 'OPENCODE_TEST_HOME': root + '/home',
            'XDG_CONFIG_HOME': root + '/config', 'XDG_DATA_HOME': root + '/data',
            'XDG_CACHE_HOME': root + '/cache', 'XDG_STATE_HOME': root + '/state',
            'TMPDIR': root + '/tmp', 'TMP': root + '/tmp', 'TEMP': root + '/tmp',
            'OPENCODE_CONFIG_DIR': root + '/config/opencode',
            'CLAUDE_CONFIG_DIR': root + '/claude', 'CODEX_HOME': root + '/codex',
            'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
        }
        name = 'skill-manager-native-test-' + uuid.uuid4().hex
        command = ['docker', 'run', '--rm', '--name', name, '--pull=never', '--network=none',
                   '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                   '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=512m',
                   '--mount', f'type=bind,src={self.binaries[harness]},dst=/opt/{harness},readonly',
                   '--mount', f'type=bind,src={root},dst={root}', '--workdir', root + '/workspace',
                   '--entrypoint', '/usr/bin/env', self.image, '-i']
        for source in ('managed', 'source'):
            if (self.root / source).is_dir():
                offset = command.index('--workdir')
                command[offset:offset] = ['--mount', f'type=bind,src={self.root / source},dst={self.root / source},readonly']
        command += [f'{key}={value}' for key, value in env.items()]
        command += [f'/opt/{harness}', *argv[1:]]
        try:
            if requests is not None:
                command.insert(command.index('--rm'), '-i')
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                        env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'DOCKER_CONFIG': '/nonexistent'})
                    try:
                        replies, pending = [], b''
                        for request in requests:
                            request_id = request['request_id'] if harness == 'claude' else request['id']
                            process.stdin.write((json.dumps(request) + '\n').encode())
                            process.stdin.flush()
                            deadline = time.monotonic() + 30
                            complete = False
                            while not complete:
                                if not select.select([process.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                                    raise TimeoutError('Native read-only RPC timed out')
                                chunk = os.read(process.stdout.fileno(), 65536)
                                if not chunk:
                                    raise RuntimeError('Native RPC closed before replying')
                                pending += chunk
                                while b'\n' in pending:
                                    line, pending = pending.split(b'\n', 1)
                                    reply = json.loads(line)
                                    # Claude SDK control frames nest their response ID; Codex uses JSON-RPC.
                                    reply_id = (reply.get('response', {}).get('request_id')
                                                if harness == 'claude' else reply.get('id'))
                                    if reply_id == request_id:
                                        replies.append(reply)
                                        complete = True
                        return replies
                    finally:
                        process.terminate()
                        process.communicate(timeout=10)
            return subprocess.run(command, text=True, capture_output=True, timeout=45,
                                  env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
                                       'DOCKER_CONFIG': '/nonexistent'})
        finally:
            # A timed-out Docker client does not necessarily stop its container.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)


def prepare(root):
    root.chmod(0o777)
    for name in ('home', 'config/opencode', 'data', 'cache', 'state', 'tmp', 'claude', 'codex', 'workspace'):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)


if __name__ == '__main__':
    from tempfile import TemporaryDirectory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    for harness in ('claude', 'codex', 'opencode'):
        parser.add_argument('--' + harness, required=True)
    parser.add_argument('--snapshot-root', action='append', required=True)
    args = parser.parse_args()
    before = snapshot(args.snapshot_root)
    results = {}
    with TemporaryDirectory(prefix='skill-manager-native-test-') as directory:
        root = Path(directory)
        prepare(root)
        run = SandboxRunner(args.image, {h: getattr(args, h) for h in ('claude', 'codex', 'opencode')}, root)
        try:
            for harness in run.binaries:
                commands = [('--version',), ('--help',)] if harness == 'opencode' else [('--version',), ('plugin', 'list', '--json')]
                for command in commands:
                    result = run([harness, *command])
                    results[harness + ' ' + ' '.join(command)] = {'code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        finally:
            run.release()
    after = snapshot(args.snapshot_root)
    print(json.dumps({'probes': results, 'before': before, 'after': after, 'unchanged': before == after}, indent=2))
    raise SystemExit(0 if before == after and all(r['code'] == 0 for r in results.values()) else 1)
