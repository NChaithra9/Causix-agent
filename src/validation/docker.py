"""Docker-isolated validation environment, driven through the ``docker`` CLI.

One throw-away container per run. The source tree is *copied in* (not
bind-mounted), so nothing the build or tests do can modify the host's files.
The container is started with a bounded ``sleep`` as PID 1, so even if
RootFix is killed mid-run the container exits on its own and (``--rm``) is
removed. Build, start and test commands run via ``docker exec``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .errors import EnvironmentError_
from .executor import run_process
from .logs import DEFAULT_LOG_LIMIT, truncate_text
from .models import CommandRun, ValidationRequest

__all__ = ["DockerCli", "DockerEnvironment", "docker_available"]

_WORKDIR = "/workspace"
_PID_FILE = "/tmp/rootfix-app.pid"  # noqa: S108 - inside the throw-away container
_LOG_FILE = "/tmp/rootfix-app.log"  # noqa: S108

# argv, timeout -> CommandRun
Runner = Callable[..., CommandRun]


class DockerCli:
    """A thin, injectable wrapper around the docker executable."""

    def __init__(self, executable: str = "docker", runner: Runner = run_process) -> None:
        self._executable = executable
        self._runner = runner

    def run(self, args: Sequence[str], *, timeout: float) -> CommandRun:
        return self._runner([self._executable, *args], timeout=timeout)

    def check(self, args: Sequence[str], *, timeout: float, what: str) -> CommandRun:
        run = self.run(args, timeout=timeout)
        if run.status.value != "PASSED":
            detail = run.error or (run.stderr.strip() or run.stdout.strip() or "no output")
            raise EnvironmentError_(
                f"{what} failed (exit {run.exit_code}): {truncate_text(detail, 2000)}"
            )
        return run


def docker_available(cli: DockerCli | None = None) -> bool:
    """True when a Docker daemon answers (used to skip Docker tests cleanly)."""
    run = (cli or DockerCli()).run(["info", "--format", "{{.ServerVersion}}"], timeout=15)
    return run.exit_code == 0


class _DockerBackground:
    def __init__(self, env: DockerEnvironment) -> None:
        self._env = env

    def is_running(self) -> bool:
        script = (
            f'p=$(cat {_PID_FILE} 2>/dev/null); [ -z "$p" ] && exit 0; '
            'kill -0 "$p" 2>/dev/null && exit 0; exit 3'
        )
        return self._env._exec(["sh", "-c", script], timeout=15).exit_code == 0

    def logs(self) -> str:
        run = self._env._exec(
            ["sh", "-c", f"tail -c {self._env._log_limit} {_LOG_FILE} 2>/dev/null"], timeout=15
        )
        return run.stdout

    def stop(self) -> None:
        self._env._exec(
            ["sh", "-c", f'kill "$(cat {_PID_FILE} 2>/dev/null)" 2>/dev/null; true'], timeout=15
        )


class DockerEnvironment:
    """A running validation container. Create it with :meth:`create`."""

    isolation = "docker"

    def __init__(
        self,
        cli: DockerCli,
        name: str,
        *,
        built_image: str | None,
        ports: Sequence[int],
        log_limit: int,
    ) -> None:
        self._cli = cli
        self._name = name
        self._built_image = built_image
        self._ports = tuple(ports)
        self._log_limit = log_limit
        self._destroyed = False

    # ---------------------------------------------------------------- creation

    @classmethod
    def create(
        cls,
        request: ValidationRequest,
        source_dir: Path,
        network: str | None,
        setup_timeout: float,
        *,
        cli: DockerCli | None = None,
        log_limit: int = DEFAULT_LOG_LIMIT,
    ) -> DockerEnvironment:
        cli = cli or DockerCli()
        name = f"rootfix-validation-{request.validation_id}"
        image, built = request.image, None

        if request.dockerfile:
            built = f"rootfix-validation-{request.validation_id}:latest"
            dockerfile = (source_dir / request.dockerfile).resolve()
            if source_dir.resolve() not in dockerfile.parents or not dockerfile.is_file():
                raise EnvironmentError_(
                    f"dockerfile {request.dockerfile!r} not found in the repository"
                )
            cli.check(
                ["build", "-t", built, "-f", str(dockerfile), str(source_dir)],
                timeout=setup_timeout,
                what="docker build",
            )
            image = built
        elif cli.run(["image", "inspect", image], timeout=30).exit_code != 0:
            cli.check(["pull", image], timeout=setup_timeout, what=f"docker pull {image}")

        # PID 1 is a bounded sleep: the container cannot outlive the run budget.
        lifetime = int(request.timeouts.overall + 120)
        args = [
            "run", "-d", "--rm", "--name", name,
            "--label", f"rootfix.validation={request.validation_id}",
            "--security-opt", "no-new-privileges",
            "-w", _WORKDIR,
        ]  # fmt: skip
        limits = request.limits
        if limits.memory:
            args += ["--memory", limits.memory, "--memory-swap", limits.memory]
        if limits.cpus:
            args += ["--cpus", str(limits.cpus)]
        if limits.pids:
            args += ["--pids-limit", str(limits.pids)]
        if network:
            args += ["--network", network]
        ports: list[int] = []
        if request.readiness and request.readiness.port:
            ports.append(request.readiness.port)
            args += ["-p", f"127.0.0.1::{request.readiness.port}/tcp"]
        args += ["--entrypoint", "sleep", image, str(lifetime)]
        cli.check(args, timeout=setup_timeout, what="docker run")

        env = cls(cli, name, built_image=built, ports=ports, log_limit=log_limit)
        try:
            cli.check(
                ["cp", f"{source_dir}/.", f"{name}:{_WORKDIR}"],
                timeout=setup_timeout,
                what="copying the source into the container",
            )
        except BaseException:
            env.destroy()
            raise
        return env

    # --------------------------------------------------------------- execution

    def _exec(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        detach: bool = False,
    ) -> CommandRun:
        args = ["exec", "-w", _WORKDIR]
        if detach:
            args.append("-d")
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [self._name, *command]
        return self._cli.run(args, timeout=timeout)

    def run(
        self, command: str, *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CommandRun:
        run = self._exec(["sh", "-c", command], timeout=timeout, env=env)
        run.command = command  # report the user's command, not the docker wrapper
        return run

    def start_background(
        self, command: str, *, env: Mapping[str, str] | None = None
    ) -> _DockerBackground:
        script = f'echo $$ > {_PID_FILE}; exec >{_LOG_FILE} 2>&1; exec sh -c "$1"'
        run = self._exec(["sh", "-c", script, "_", command], timeout=60, env=env, detach=True)
        if run.exit_code != 0:
            raise EnvironmentError_(
                f"could not start the application: {run.stderr.strip() or run.error}"
            )
        return _DockerBackground(self)

    def app_address(self, port: int) -> tuple[str, int]:
        if port not in self._ports:
            raise EnvironmentError_(f"port {port} was not published; set it in the readiness spec")
        run = self._cli.run(["port", self._name, f"{port}/tcp"], timeout=15)
        match = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", run.stdout)
        if run.exit_code != 0 or not match:
            raise EnvironmentError_(
                f"could not resolve the published port {port}: {run.stderr.strip()}"
            )
        return match.group(1), int(match.group(2))

    def infrastructure_logs(self) -> str:
        run = self._cli.run(["logs", "--tail", "200", self._name], timeout=30)
        return (run.stdout + run.stderr).strip()

    def destroy(self) -> None:
        if self._destroyed:
            return
        errors = []
        run = self._cli.run(["rm", "-f", self._name], timeout=60)
        if run.exit_code != 0 and "No such container" not in run.stderr:
            errors.append(f"docker rm failed: {run.stderr.strip() or run.error}")
        if self._built_image:
            run = self._cli.run(["rmi", "-f", self._built_image], timeout=60)
            if run.exit_code != 0 and "No such image" not in run.stderr:
                errors.append(f"docker rmi failed: {run.stderr.strip() or run.error}")
        if errors:
            raise EnvironmentError_("; ".join(errors))
        self._destroyed = True
