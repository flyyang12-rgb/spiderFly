"""One frozen Python package, one owned process tree, one durable outcome."""

import hashlib
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

import psutil

from .windows import ProcessJob

MAX_ARTIFACT = 16 * 1024 * 1024
_GATE = "import subprocess,sys\nif sys.stdin.buffer.readline()!=b'GO\\n': sys.exit(125)\nsys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))"


class Interrupted(RuntimeError):
    def __init__(self, status: str):
        self.status = status
        super().__init__("任务已停止" if status == "cancelled" else "任务超时（含依赖准备时间）")


class Uncertain(RuntimeError):
    pass


def package_hash(script: str, requirements: str) -> str:
    return hashlib.sha256(script.encode("utf-8") + b"\0" + requirements.encode("utf-8")).hexdigest()


def validate_artifact_name(name: str):
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not name or len(name) > 160 or name in {".", ".."} or name.endswith((".", " "))
            or any(char in name for char in '/\\:<>"|?*') or any(ord(char) < 32 for char in name)
            or name.split(".")[0].upper() in reserved):
        raise ValueError("产物文件名不安全（仅文件名、最多 160 字符）")


def safe_artifact(path: Path, directory: Path) -> bytes:
    validate_artifact_name(path.name)
    # Reject directory junctions/symlinks as well as file links. The task can alter
    # its working directory, so the trusted boundary must not move with resolve().
    absolute_directory = Path(os.path.abspath(directory))
    if directory.is_symlink() or directory.resolve() != absolute_directory:
        raise ValueError("产物目录被替换为链接或目录外路径")
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError("产物必须是本次目录内的普通文件")
    if path.stat().st_size > MAX_ARTIFACT:
        raise ValueError("产物超过 16 MiB 限制")
    data = path.read_bytes()
    if len(data) > MAX_ARTIFACT:
        raise ValueError("产物超过 16 MiB 限制")
    return data


class Worker(threading.Thread):
    def __init__(self, run: dict, root: Path, api, journal, observer=None):
        super().__init__(name=f"spiderfly-run-{run['id']}", daemon=False)
        self.run_info, self.root, self.api, self.journal = run, root.resolve(), api, journal
        self.run_id = int(run["id"])
        self.stop_event = threading.Event()
        self.observer = observer
        self.deadline = 0.0
        self.work = self.root / "runs" / str(self.run_id)
        self.artifacts = self.work / "artifacts"

    def emit(self, kind: str, text: str):
        self.journal.append(self.run_id, kind, text)
        if self.observer:
            try:
                self.observer(kind, text)
            except Exception:
                # Desktop presentation must never affect durable execution.
                pass

    def check_stop(self):
        if self.stop_event.is_set():
            raise Interrupted("cancelled")
        if time.monotonic() >= self.deadline:
            raise Interrupted("timed_out")

    def environment(self) -> dict:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("SPIDERFLY_", "FEISHU_", "DEEPSEEK_", "OPENAI_"))
               and key not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "SPIDERFLY_ARTIFACT_DIR": str(self.artifacts),
                    "SPIDERFLY_RESULT_FILE": str(self.artifacts / "result.json")})
        return env

    def command(self, command: list[str]) -> int:
        self.check_stop()
        self.journal.set_process(self.run_id, state="spawning")
        job, process = ProcessJob(), None
        readers = []
        output = queue.Queue(maxsize=256)
        try:
            process = subprocess.Popen([sys.executable, "-u", "-c", _GATE, *command],
                cwd=self.work, env=self.environment(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt")
            job.assign(process)
            birth = psutil.Process(process.pid).create_time()
            self.journal.set_process(self.run_id, process.pid, birth, job_owned=bool(job.handle))
            # The command cannot run before durable PID ownership and job assignment.
            process.stdin.write(b"GO\n")
            process.stdin.flush()
            process.stdin.close()

            def read(stream, kind):
                import codecs
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                try:
                    while True:
                        chunk = stream.read1(4096)
                        if not chunk:
                            break
                        text = decoder.decode(chunk)
                        if text:
                            output.put((kind, text))
                    text = decoder.decode(b"", final=True)
                    if text:
                        output.put((kind, text))
                finally:
                    stream.close()

            for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                reader = threading.Thread(target=read, args=(stream, kind), daemon=True)
                reader.start()
                readers.append(reader)
            while process.poll() is None:
                self.check_stop()
                self._drain(output)
                self.stop_event.wait(0.1)
            exit_code = process.returncode
            if not self._terminate(process, job):
                raise Uncertain("无法确认本次子进程树全部退出")
            self._finish_output(readers, output)
            self.journal.set_process(self.run_id, state="preparing")
            return exit_code
        except BaseException:
            if process is not None:
                if not self._terminate(process, job):
                    raise Uncertain("停止后无法确认本次进程树全部退出") from None
                self._finish_output(readers, output)
            raise
        finally:
            job.close()
            if process:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()

    def _drain(self, output):
        for _ in range(256):
            try:
                kind, text = output.get_nowait()
            except queue.Empty:
                return
            self.emit(kind, text)

    def _finish_output(self, readers, output):
        deadline = time.monotonic() + 5
        while any(reader.is_alive() for reader in readers) and time.monotonic() < deadline:
            self._drain(output)
            for reader in readers:
                reader.join(0.05)
        self._drain(output)

    @staticmethod
    def _terminate(process, job) -> bool:
        try:
            if job.handle:
                job.terminate()
                deadline = time.monotonic() + 5
                while job.active_count() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if job.active_count():
                    return False
                process.wait(timeout=5)
                return True
            if os.name != "nt":
                # Portable synthetic tests only; production CLI requires Windows.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                # Assignment failed before GO; no business process was allowed to start.
                process.kill()
            process.wait(timeout=5)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def prepare_environment(self, requirements: str, content_hash: str) -> Path:
        # A full SHA-256 directory plus pip's nested files exceeds MAX_PATH in
        # common temp/deployment roots. Keep a 128-bit prefix in the path, but
        # validate/store the entire digest so a collision never reuses an env.
        cache = self.root / "envs" / content_hash[:32]
        cache.mkdir(parents=True, exist_ok=True)
        digest_file = cache / ".package-hash"
        if digest_file.exists():
            if digest_file.read_text(encoding="ascii") != content_hash:
                raise RuntimeError("环境缓存哈希前缀冲突，拒绝复用不同内容的环境")
        else:
            digest_file.write_text(content_hash, encoding="ascii")
        python = cache / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        marker = cache / ".ready"
        if marker.is_file() and python.is_file() and marker.read_text(encoding="ascii") == content_hash:
            return python
        self.emit("stdout", "准备本机 Python 环境…\n")
        if self.command([sys.executable, "-m", "venv", str(cache)]) != 0:
            hint = "；数据目录路径过长时请使用较短的 --data-dir" if os.name == "nt" else ""
            raise RuntimeError(f"创建 Python 环境失败，详见标准错误{hint}")
        if requirements.strip():
            requirement_file = self.work / "requirements.txt"
            requirement_file.write_text(requirements, encoding="utf-8", newline="")
            if self.command([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                             "--no-input", "-r", str(requirement_file)]) != 0:
                raise RuntimeError("安装 Python 依赖失败，详见标准错误")
        self.check_stop()
        marker.write_text(content_hash, encoding="ascii")
        return python

    def run(self):
        self.deadline = time.monotonic() + max(1, int(self.run_info["timeout_seconds"]))
        status, text, exit_code = "failed", "执行端未完成运行", None
        try:
            expected = self.run_info["package_hash"]
            if not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError("无效的任务包哈希")
            self.work.mkdir(parents=True, exist_ok=False)
            self.artifacts.mkdir()
            (self.work / "inputs").mkdir()
            self.emit("stdout", "正在下载冻结任务包…\n")
            self.check_stop()
            package = self.api.request("GET", f"/api/agent/runs/{self.run_id}/package")
            if package.get("stop_requested") is True:
                raise Interrupted("cancelled")
            script, requirements = package["script"], package["requirements"]
            if package["package_hash"] != expected or package_hash(script, requirements) != expected:
                raise ValueError("任务包内容哈希不匹配")
            self.check_stop()
            entrypoint = self.work / "main.py"
            entrypoint.write_text(script, encoding="utf-8", newline="")
            python = self.prepare_environment(requirements, expected)
            self.emit("started", "开始执行")
            exit_code = self.command([str(python), "-u", str(entrypoint)])
            status = "succeeded" if exit_code == 0 else "failed"
            text = "执行成功" if exit_code == 0 else f"Python 退出码 {exit_code}"
        except Interrupted as error:
            status, text = error.status, str(error)
        except Uncertain as error:
            self.journal.uncertain(self.run_id, str(error))
            return
        except Exception as error:
            text = f"{type(error).__name__}: {error}"
            self.emit("stderr", text)
        try:
            if self.artifacts.exists():
                total_size, count = 0, 0
                names = set()
                for path in sorted(self.artifacts.iterdir()):
                    try:
                        data = safe_artifact(path, self.artifacts)
                        if path.name.casefold() in names:
                            raise ValueError("产物文件名大小写折叠后冲突")
                        if count >= 100 or total_size + len(data) > 64 * 1024 * 1024:
                            raise ValueError("单次运行最多回传 100 个文件、合计 64 MiB")
                        self.journal.add_artifact(self.run_id, path.name, path, hashlib.sha256(data).hexdigest())
                        total_size += len(data)
                        count += 1
                        names.add(path.name.casefold())
                    except (OSError, ValueError) as error:
                        self.emit("stderr", f"未上传产物 {path.name}: {error}")
                        if status == "succeeded":
                            status, text = "failed", "Python 执行结束，但部分产物不符合回传要求，详见错误日志"
            self.journal.complete(self.run_id, status, text, exit_code)
        except Exception as error:
            # Never let a thread failure silently make this host idle.
            self.journal.uncertain(self.run_id, f"结果持久化失败: {type(error).__name__}")
