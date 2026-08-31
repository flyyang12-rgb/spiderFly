using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("SpiderFly Launcher")]
[assembly: AssemblyDescription("SpiderFly Windows background launcher")]
[assembly: AssemblyCompany("SpiderFly")]
[assembly: AssemblyProduct("SpiderFly")]
[assembly: AssemblyVersion("1.0.0.0")]

namespace SpiderFlyLauncher
{
    internal enum ProbeStatus
    {
        Unavailable,
        Ready,
        SpiderFlyPresent,
        PortOccupied
    }

    internal sealed class LogWriter : IDisposable
    {
        private const long MaxLogBytes = 5L * 1024L * 1024L;
        private readonly object syncRoot = new object();
        private readonly string logPath;
        private StreamWriter writer;

        public LogWriter(string logPath)
        {
            this.logPath = logPath;
            string directory = Path.GetDirectoryName(logPath);
            if (!String.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            try
            {
                RotateIfNeeded(logPath);
            }
            catch (IOException)
            {
                // Another launcher may still be supervising the active server.
                // In that case append to the current log instead of blocking a
                // harmless repeat launch just because the file cannot rotate.
            }
            catch (UnauthorizedAccessException)
            {
                // Opening the log below will still report a real write failure.
            }
            writer = OpenWriter();
        }

        private StreamWriter OpenWriter()
        {
            FileStream stream = new FileStream(
                this.logPath,
                FileMode.Append,
                FileAccess.Write,
                FileShare.ReadWrite
            );
            StreamWriter result = new StreamWriter(stream, new UTF8Encoding(false));
            result.AutoFlush = true;
            return result;
        }

        public void Write(string source, string message)
        {
            if (String.IsNullOrWhiteSpace(message))
            {
                return;
            }

            lock (syncRoot)
            {
                RotateOpenLogIfNeeded();
                writer.WriteLine(
                    "{0:yyyy-MM-dd HH:mm:ss.fff} [{1}] {2}",
                    DateTime.Now,
                    source,
                    message
                );
            }
        }

        private void RotateOpenLogIfNeeded()
        {
            if (writer.BaseStream.Length < MaxLogBytes)
            {
                return;
            }

            writer.Dispose();
            try
            {
                RotateIfNeeded(logPath);
            }
            catch (IOException)
            {
                // A harmless repeat launcher may still have the file open.
            }
            catch (UnauthorizedAccessException)
            {
                // Reopen below so logging can continue even without rotation.
            }
            finally
            {
                writer = OpenWriter();
            }
        }

        public void Dispose()
        {
            lock (syncRoot)
            {
                writer.Dispose();
            }
        }

        private static void RotateIfNeeded(string logPath)
        {
            FileInfo current = new FileInfo(logPath);
            if (!current.Exists || current.Length < MaxLogBytes)
            {
                return;
            }

            string previous = logPath + ".1";
            if (File.Exists(previous))
            {
                File.Delete(previous);
            }
            File.Move(logPath, previous);
        }
    }

    internal sealed class KillOnCloseJob : IDisposable
    {
        private const uint JobObjectLimitKillOnJobClose = 0x00002000;
        private const int JobObjectExtendedLimitInformation = 9;
        private IntPtr handle;

        private KillOnCloseJob(IntPtr handle)
        {
            this.handle = handle;
        }

        public static KillOnCloseJob TryCreate(LogWriter log)
        {
            IntPtr jobHandle = CreateJobObject(IntPtr.Zero, null);
            if (jobHandle == IntPtr.Zero)
            {
                log.Write(
                    "ERROR",
                    "Windows 进程组创建失败，错误码：" + Marshal.GetLastWin32Error()
                );
                return null;
            }

            JobObjectExtendedLimitInformationData information =
                new JobObjectExtendedLimitInformationData();
            information.BasicLimitInformation.LimitFlags =
                JobObjectLimitKillOnJobClose;
            int length = Marshal.SizeOf(typeof(JobObjectExtendedLimitInformationData));
            if (
                !SetInformationJobObject(
                    jobHandle,
                    JobObjectExtendedLimitInformation,
                    ref information,
                    (uint)length
                )
            )
            {
                int error = Marshal.GetLastWin32Error();
                CloseHandle(jobHandle);
                log.Write(
                    "ERROR",
                    "Windows 进程组保护设置失败，错误码：" + error
                );
                return null;
            }
            return new KillOnCloseJob(jobHandle);
        }

        public bool Assign(Process process, LogWriter log)
        {
            if (handle == IntPtr.Zero)
            {
                return false;
            }
            if (AssignProcessToJobObject(handle, process.Handle))
            {
                log.Write("LAUNCHER", "服务进程已加入随启动器关闭的 Windows 进程组。");
                return true;
            }
            log.Write(
                "ERROR",
                "服务进程未能加入 Windows 进程组，错误码：" + Marshal.GetLastWin32Error()
            );
            return false;
        }

        public void Dispose()
        {
            if (handle != IntPtr.Zero)
            {
                CloseHandle(handle);
                handle = IntPtr.Zero;
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimitInformationData
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JobObjectExtendedLimitInformationData
        {
            public BasicLimitInformationData BasicLimitInformation;
            public IoCounters IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(
            IntPtr jobAttributes,
            string name
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            int informationClass,
            ref JobObjectExtendedLimitInformationData information,
            uint informationLength
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(
            IntPtr job,
            IntPtr process
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
    }

    internal static class Program
    {
        private const string ProductName = "SpiderFly";
        private const string LauncherMutexName =
            "Global\\SpiderFly.Launcher.04C5438E-83B3-49D8-8868-630EEDC79E5C";
        private const int DefaultPort = 8000;
        private const int StartupWaitSeconds = 30;

        [STAThread]
        private static int Main(string[] args)
        {
            bool startupMode = HasArgument(args, "--startup");
            try
            {
                return Run(startupMode);
            }
            catch (Exception exception)
            {
                if (!startupMode)
                {
                    ShowError(
                        "SpiderFly 启动器发生错误：\r\n\r\n" + exception.Message
                    );
                }
                return 1;
            }
        }

        private static int Run(bool startupMode)
        {
            string projectRoot = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
            IDictionary<string, string> fileSettings = LoadEnvFile(
                Path.Combine(projectRoot, ".env")
            );
            string dataDirectory = ResolveDataDirectory(projectRoot, fileSettings);
            string logPath = Path.Combine(dataDirectory, "logs", "spiderfly.log");

            using (LogWriter log = new LogWriter(logPath))
            {
                log.Write(
                    "LAUNCHER",
                    startupMode
                        ? "收到 Windows 登录自启请求。"
                        : "收到用户手动启动请求。"
                );

                string host = GetSetting("SPIDERFLY_HOST", fileSettings, "0.0.0.0");
                int port = GetPort(fileSettings);
                string browserUrl = BuildBrowserUrl(host, port);
                string healthUrl = browserUrl.TrimEnd('/') + "/health";

                ProbeStatus initial = Probe(healthUrl);
                if (initial == ProbeStatus.Ready || initial == ProbeStatus.SpiderFlyPresent)
                {
                    log.Write("LAUNCHER", "检测到 SpiderFly 已经运行，不再启动第二个进程。");
                    if (startupMode)
                    {
                        return MonitorExistingService(healthUrl, log);
                    }
                    else
                    {
                        OpenBrowser(browserUrl, log);
                    }
                    return 0;
                }

                string pythonPath = Path.Combine(
                    projectRoot,
                    ".venv",
                    "Scripts",
                    "python.exe"
                );
                string backendDirectory = Path.Combine(projectRoot, "backend");
                string mainFile = Path.Combine(backendDirectory, "app", "main.py");
                string frontendIndex = Path.Combine(
                    projectRoot,
                    "frontend",
                    "dist",
                    "index.html"
                );

                if (!File.Exists(pythonPath))
                {
                    return Fail(
                        startupMode,
                        log,
                        "没有找到 SpiderFly 自己的 Python 环境：" + pythonPath,
                        logPath
                    );
                }
                if (!File.Exists(mainFile))
                {
                    return Fail(
                        startupMode,
                        log,
                        "没有找到 SpiderFly 后端程序：" + mainFile,
                        logPath
                    );
                }
                if (!File.Exists(frontendIndex))
                {
                    return Fail(
                        startupMode,
                        log,
                        "没有找到已经构建好的管理页面：" + frontendIndex,
                        logPath
                    );
                }

                bool ownsMutex;
                using (Mutex launcherMutex = new Mutex(
                    true,
                    LauncherMutexName,
                    out ownsMutex
                ))
                {
                    if (!ownsMutex)
                    {
                        log.Write("LAUNCHER", "另一份启动器正在启动或守护 SpiderFly，等待其就绪。");
                        bool appeared = WaitForExistingSpiderFly(healthUrl, StartupWaitSeconds);
                        if (appeared)
                        {
                            if (startupMode)
                            {
                                return MonitorExistingService(healthUrl, log);
                            }
                            else
                            {
                                OpenBrowser(browserUrl, log);
                            }
                            return 0;
                        }
                        return Fail(
                            startupMode,
                            log,
                            "另一份 SpiderFly 启动器仍在工作，但服务没有在 30 秒内就绪。",
                            logPath
                        );
                    }

                    try
                    {
                        if (initial == ProbeStatus.PortOccupied)
                        {
                            return Fail(
                                startupMode,
                                log,
                                "端口 " + port + " 已被其他程序占用，SpiderFly 无法启动。",
                                logPath
                            );
                        }
                        return StartAndSupervise(
                            pythonPath,
                            backendDirectory,
                            projectRoot,
                            host,
                            port,
                            browserUrl,
                            healthUrl,
                            startupMode,
                            log,
                            logPath
                        );
                    }
                    finally
                    {
                        launcherMutex.ReleaseMutex();
                    }
                }
            }
        }

        private static int StartAndSupervise(
            string pythonPath,
            string backendDirectory,
            string projectRoot,
            string host,
            int port,
            string browserUrl,
            string healthUrl,
            bool startupMode,
            LogWriter log,
            string logPath
        )
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = pythonPath;
            info.Arguments = String.Join(
                " ",
                new string[]
                {
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--app-dir",
                    Quote(backendDirectory),
                    "--host",
                    Quote(host),
                    "--port",
                    port.ToString(),
                    "--workers",
                    "1"
                }
            );
            info.WorkingDirectory = projectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";

            using (KillOnCloseJob processJob = KillOnCloseJob.TryCreate(log))
            using (Process child = new Process())
            {
                child.StartInfo = info;
                child.EnableRaisingEvents = true;
                child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
                {
                    if (eventArgs.Data != null)
                    {
                        try
                        {
                            log.Write("SERVER", eventArgs.Data);
                        }
                        catch
                        {
                            // Never let a transient log write failure crash the
                            // supervisor and leave a running child unattended.
                        }
                    }
                };
                child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
                {
                    if (eventArgs.Data != null)
                    {
                        try
                        {
                            log.Write("SERVER", eventArgs.Data);
                        }
                        catch
                        {
                            // Keep draining stderr even if the log disk has a
                            // temporary write problem.
                        }
                    }
                };

                log.Write("LAUNCHER", "正在后台启动 SpiderFly 服务。");
                if (!child.Start())
                {
                    return Fail(
                        startupMode,
                        log,
                        "Windows 没有成功创建 SpiderFly 服务进程。",
                        logPath
                    );
                }
                if (processJob != null)
                {
                    processJob.Assign(child, log);
                }
                child.BeginOutputReadLine();
                child.BeginErrorReadLine();
                log.Write("LAUNCHER", "服务进程 PID：" + child.Id);

                bool ready = WaitForReady(child, healthUrl, StartupWaitSeconds);
                if (ready)
                {
                    log.Write("LAUNCHER", "SpiderFly 健康检查通过。");
                    if (!startupMode)
                    {
                        OpenBrowser(browserUrl, log);
                    }
                }
                else
                {
                    Fail(
                        startupMode,
                        log,
                        child.HasExited
                            ? "SpiderFly 服务在准备完成前退出。"
                            : "SpiderFly 服务没有在 30 秒内通过健康检查。",
                        logPath
                    );
                    if (!child.HasExited)
                    {
                        try
                        {
                            child.Kill();
                            child.WaitForExit(5000);
                            log.Write("LAUNCHER", "未就绪的服务进程已经结束。");
                        }
                        catch (Exception exception)
                        {
                            log.Write(
                                "ERROR",
                                "结束未就绪服务进程失败：" + exception.Message
                            );
                        }
                    }
                }

                if (!child.HasExited)
                {
                    child.WaitForExit();
                }
                child.WaitForExit();
                int exitCode = child.ExitCode;
                log.Write("LAUNCHER", "SpiderFly 服务已经退出，退出码：" + exitCode);
                return ready ? exitCode : (exitCode == 0 ? 1 : exitCode);
            }
        }

        private static bool WaitForReady(Process child, string healthUrl, int seconds)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                if (child.HasExited)
                {
                    return false;
                }
                if (Probe(healthUrl) == ProbeStatus.Ready)
                {
                    return true;
                }
                Thread.Sleep(500);
            }
            return false;
        }

        private static bool WaitForExistingSpiderFly(string healthUrl, int seconds)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                ProbeStatus status = Probe(healthUrl);
                if (status == ProbeStatus.Ready || status == ProbeStatus.SpiderFlyPresent)
                {
                    return true;
                }
                Thread.Sleep(500);
            }
            return false;
        }

        private static int MonitorExistingService(string healthUrl, LogWriter log)
        {
            log.Write("LAUNCHER", "登录自启进入健康守望，不会打开浏览器。");
            int consecutiveFailures = 0;
            while (true)
            {
                Thread.Sleep(5000);
                ProbeStatus status = Probe(healthUrl);
                if (status == ProbeStatus.Ready)
                {
                    if (consecutiveFailures > 0)
                    {
                        log.Write("LAUNCHER", "SpiderFly 健康检查已经恢复。");
                    }
                    consecutiveFailures = 0;
                    continue;
                }

                consecutiveFailures += 1;
                if (consecutiveFailures == 1)
                {
                    log.Write("ERROR", "SpiderFly 健康检查暂时不可用，正在复核。");
                }
                if (consecutiveFailures >= 3)
                {
                    log.Write(
                        "ERROR",
                        "SpiderFly 连续 3 次健康检查不可用，交给 Windows 计划任务重新启动。"
                    );
                    return 1;
                }
            }
        }

        private static ProbeStatus Probe(string healthUrl)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(healthUrl);
                request.Method = "GET";
                request.Proxy = null;
                request.Timeout = 1200;
                request.ReadWriteTimeout = 1200;
                request.KeepAlive = false;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                {
                    string body = reader.ReadToEnd();
                    return ClassifyResponse((int)response.StatusCode, body);
                }
            }
            catch (WebException exception)
            {
                if (exception.Response != null)
                {
                    using (WebResponse response = exception.Response)
                    using (Stream stream = response.GetResponseStream())
                    using (StreamReader reader = stream == null ? null : new StreamReader(stream))
                    {
                        string body = reader == null ? String.Empty : reader.ReadToEnd();
                        if (body.IndexOf("shared-central-python", StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            return ProbeStatus.SpiderFlyPresent;
                        }
                    }
                    return ProbeStatus.PortOccupied;
                }

                if (!IsLocalPortListening(healthUrl))
                {
                    return ProbeStatus.Unavailable;
                }

                if (
                    exception.Status == WebExceptionStatus.ConnectFailure
                    || exception.Status == WebExceptionStatus.ConnectionClosed
                    || exception.Status == WebExceptionStatus.NameResolutionFailure
                )
                {
                    return ProbeStatus.Unavailable;
                }
                return ProbeStatus.PortOccupied;
            }
            catch
            {
                return ProbeStatus.PortOccupied;
            }
        }

        private static bool IsLocalPortListening(string healthUrl)
        {
            try
            {
                int port = new Uri(healthUrl).Port;
                foreach (
                    System.Net.IPEndPoint endpoint
                    in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners()
                )
                {
                    if (endpoint.Port == port)
                    {
                        return true;
                    }
                }
                return false;
            }
            catch
            {
                // If Windows refuses the read-only listener query, keep the
                // conservative result and avoid starting on an unknown port.
                return true;
            }
        }

        private static ProbeStatus ClassifyResponse(int statusCode, string body)
        {
            if (body.IndexOf("shared-central-python", StringComparison.OrdinalIgnoreCase) < 0)
            {
                return ProbeStatus.PortOccupied;
            }

            string compact = body
                .Replace(" ", String.Empty)
                .Replace("\t", String.Empty)
                .Replace("\r", String.Empty)
                .Replace("\n", String.Empty);
            bool workersReady =
                compact.IndexOf("\"scheduler\":\"running\"", StringComparison.OrdinalIgnoreCase) >= 0
                && compact.IndexOf("\"queue_worker\":\"running\"", StringComparison.OrdinalIgnoreCase) >= 0
                && compact.IndexOf("\"environment_worker\":\"running\"", StringComparison.OrdinalIgnoreCase) >= 0;
            return statusCode == 200 && workersReady
                ? ProbeStatus.Ready
                : ProbeStatus.SpiderFlyPresent;
        }

        private static IDictionary<string, string> LoadEnvFile(string path)
        {
            Dictionary<string, string> values = new Dictionary<string, string>(
                StringComparer.OrdinalIgnoreCase
            );
            if (!File.Exists(path))
            {
                return values;
            }

            foreach (string rawLine in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = rawLine.Trim();
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                {
                    continue;
                }
                int separator = line.IndexOf('=');
                if (separator <= 0)
                {
                    continue;
                }
                string key = line.Substring(0, separator).Trim();
                string value = line.Substring(separator + 1).Trim();
                if (
                    value.Length >= 2
                    && (
                        (value[0] == '"' && value[value.Length - 1] == '"')
                        || (value[0] == '\'' && value[value.Length - 1] == '\'')
                    )
                )
                {
                    value = value.Substring(1, value.Length - 2);
                }
                if (key.Length > 0)
                {
                    values[key] = value;
                }
            }
            return values;
        }

        private static string GetSetting(
            string name,
            IDictionary<string, string> fileSettings,
            string defaultValue
        )
        {
            string processValue = Environment.GetEnvironmentVariable(name);
            if (!String.IsNullOrWhiteSpace(processValue))
            {
                return processValue.Trim();
            }
            string fileValue;
            if (fileSettings.TryGetValue(name, out fileValue) && !String.IsNullOrWhiteSpace(fileValue))
            {
                return fileValue.Trim();
            }
            return defaultValue;
        }

        private static int GetPort(IDictionary<string, string> fileSettings)
        {
            int port;
            string raw = GetSetting(
                "SPIDERFLY_PORT",
                fileSettings,
                DefaultPort.ToString()
            );
            return Int32.TryParse(raw, out port) && port >= 1 && port <= 65535
                ? port
                : DefaultPort;
        }

        private static string ResolveDataDirectory(
            string projectRoot,
            IDictionary<string, string> fileSettings
        )
        {
            string configured = GetSetting("SPIDERFLY_DATA_DIR", fileSettings, "data");
            return Path.IsPathRooted(configured)
                ? Path.GetFullPath(configured)
                : Path.GetFullPath(Path.Combine(projectRoot, configured));
        }

        private static string BuildBrowserUrl(string host, int port)
        {
            string browserHost = host.Trim();
            if (browserHost == "0.0.0.0")
            {
                browserHost = "127.0.0.1";
            }
            else if (browserHost == "::")
            {
                browserHost = "::1";
            }
            if (browserHost.IndexOf(':') >= 0 && !browserHost.StartsWith("[", StringComparison.Ordinal))
            {
                browserHost = "[" + browserHost + "]";
            }
            return "http://" + browserHost + ":" + port + "/";
        }

        private static bool HasArgument(string[] args, string expected)
        {
            foreach (string argument in args)
            {
                if (String.Equals(argument, expected, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void OpenBrowser(string url, LogWriter log)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(url);
                info.UseShellExecute = true;
                Process.Start(info);
                log.Write("LAUNCHER", "已打开 SpiderFly 管理页面。");
            }
            catch (Exception exception)
            {
                log.Write("LAUNCHER", "浏览器打开失败：" + exception.Message);
                ShowError(
                    "SpiderFly 已经运行，但浏览器没有自动打开。\r\n\r\n请手动访问："
                    + url
                );
            }
        }

        private static int Fail(
            bool startupMode,
            LogWriter log,
            string message,
            string logPath
        )
        {
            log.Write("ERROR", message);
            if (!startupMode)
            {
                ShowError(message + "\r\n\r\n详细日志：" + logPath);
            }
            return 1;
        }

        private static void ShowError(string message)
        {
            MessageBox.Show(
                message,
                ProductName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
