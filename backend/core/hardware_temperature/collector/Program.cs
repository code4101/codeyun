using System.Diagnostics;
using System.Management;
using System.Security.Principal;
using System.Text.Json;
using LibreHardwareMonitor.Hardware;

var options = CollectorOptions.Parse(args);
var computer = new Computer
{
    IsCpuEnabled = true,
    IsGpuEnabled = true,
    IsMotherboardEnabled = true,
    IsStorageEnabled = false,
};
computer.Open();
var visitor = new UpdateVisitor();

try
{
    do
    {
        if (options.ParentPid is int parentPid && !ProcessExists(parentPid)) break;
        computer.Accept(visitor);
        var snapshot = SnapshotBuilder.Build(computer);
        AtomicJson.Write(options.Output, snapshot);
        if (options.Once) break;
        Thread.Sleep(options.IntervalMs);
    } while (true);
}
finally
{
    computer.Close();
}

static bool ProcessExists(int pid)
{
    try { return !Process.GetProcessById(pid).HasExited; }
    catch { return false; }
}

sealed record CollectorOptions(string Output, int IntervalMs, bool Once, int? ParentPid)
{
    public static CollectorOptions Parse(string[] args)
    {
        string? output = null;
        var intervalMs = 1000;
        var once = false;
        int? parentPid = null;
        for (var index = 0; index < args.Length; index++)
        {
            switch (args[index])
            {
                case "--output" when index + 1 < args.Length:
                    output = args[++index];
                    break;
                case "--interval-ms" when index + 1 < args.Length:
                    intervalMs = Math.Clamp(int.Parse(args[++index]), 500, 60_000);
                    break;
                case "--parent-pid" when index + 1 < args.Length:
                    parentPid = int.Parse(args[++index]);
                    break;
                case "--once":
                    once = true;
                    break;
            }
        }
        if (string.IsNullOrWhiteSpace(output)) throw new ArgumentException("--output is required");
        return new CollectorOptions(Path.GetFullPath(output), intervalMs, once, parentPid);
    }
}

sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) => computer.Traverse(this);
    public void VisitHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (var subHardware in hardware.SubHardware) subHardware.Accept(this);
    }
    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

static class SnapshotBuilder
{
    public static object Build(Computer computer)
    {
        var devices = new List<DeviceReading>();
        foreach (var hardware in computer.Hardware)
        {
            AddHardware(devices, hardware);
        }
        devices.AddRange(StorageReader.Read());
        var incomplete = devices.Any(device => device.Sensors.Count == 0);
        return new
        {
            status = devices.Any(device => device.Sensors.Count > 0) ? (incomplete ? "partial" : "ok") : "unavailable",
            observed_at = DateTimeOffset.Now,
            collector = "codeyun-hardware-temperature-collector",
            elevated = IsElevated(),
            devices,
        };
    }

    private static void AddHardware(List<DeviceReading> devices, IHardware hardware)
    {
        var kind = KindOf(hardware.HardwareType);
        var sensors = hardware.Sensors
            .Where(sensor => sensor.SensorType == SensorType.Temperature && Valid(sensor.Value))
            .Select(sensor => new SensorReading(
                sensor.Identifier.ToString(), sensor.Name, Math.Round(sensor.Value!.Value, 1), "librehardwaremonitor"))
            .ToList();
        if (kind is not null && (sensors.Count > 0 || kind == "cpu"))
        {
            devices.Add(new DeviceReading(
                hardware.Identifier.ToString(), kind, hardware.Name, [],
                sensors.Count == 0 ? null : sensors.Max(sensor => sensor.Value), sensors));
        }
        foreach (var child in hardware.SubHardware) AddHardware(devices, child);
    }

    private static string? KindOf(HardwareType type) => type switch
    {
        HardwareType.Cpu => "cpu",
        HardwareType.GpuAmd or HardwareType.GpuIntel or HardwareType.GpuNvidia => "gpu",
        HardwareType.Motherboard or HardwareType.SuperIO => "motherboard",
        _ => null,
    };

    internal static bool Valid(float? value) => value is > 0 and <= 150 && float.IsFinite(value.Value);

    private static bool IsElevated()
    {
        using var identity = WindowsIdentity.GetCurrent();
        return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }
}

static class StorageReader
{
    public static List<DeviceReading> Read()
    {
        var disks = ReadWindowsDisks();
        var smartctl = FindSmartctl();
        if (smartctl is null) return disks.Values.OrderBy(device => device.Id).ToList();
        foreach (var device in ScanDevices(smartctl))
        {
            var index = DeviceIndex(device.Name);
            if (index is null || !disks.TryGetValue(index.Value, out var disk)) continue;
            var sensors = ReadSmartTemperatures(smartctl, device);
            if (sensors.Count == 0) continue;
            disks[index.Value] = disk with
            {
                Temperature = sensors.Max(sensor => sensor.Value),
                Sensors = sensors,
            };
        }
        return disks.Values.OrderBy(device => device.Id).ToList();
    }

    private static Dictionary<int, DeviceReading> ReadWindowsDisks()
    {
        var disks = new Dictionary<int, DeviceReading>();
        using var searcher = new ManagementObjectSearcher("SELECT Index, Model FROM Win32_DiskDrive");
        foreach (ManagementObject disk in searcher.Get())
        {
            var index = Convert.ToInt32(disk["Index"]);
            var letters = ReadDriveLetters(index);
            disks[index] = new DeviceReading(
                $"storage:{index}", "storage", Convert.ToString(disk["Model"])?.Trim() ?? $"PhysicalDrive{index}",
                letters, null, []);
        }
        return disks;
    }

    private static List<string> ReadDriveLetters(int diskIndex)
    {
        var letters = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var partitionQuery = $"ASSOCIATORS OF {{Win32_DiskDrive.DeviceID='\\\\.\\PHYSICALDRIVE{diskIndex}'}} WHERE AssocClass=Win32_DiskDriveToDiskPartition";
        using var partitions = new ManagementObjectSearcher(partitionQuery);
        foreach (ManagementObject partition in partitions.Get())
        {
            var deviceId = Convert.ToString(partition["DeviceID"])?.Replace("\\", "\\\\").Replace("'", "\\'");
            if (string.IsNullOrWhiteSpace(deviceId)) continue;
            using var logicals = new ManagementObjectSearcher($"ASSOCIATORS OF {{Win32_DiskPartition.DeviceID='{deviceId}'}} WHERE AssocClass=Win32_LogicalDiskToPartition");
            foreach (ManagementObject logical in logicals.Get())
            {
                var name = Convert.ToString(logical["DeviceID"]);
                if (!string.IsNullOrWhiteSpace(name)) letters.Add(name);
            }
        }
        return letters.OrderBy(value => value).ToList();
    }

    private static string? FindSmartctl()
    {
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "smartmontools", "bin", "smartctl.exe"),
            "smartctl.exe",
        };
        return candidates.FirstOrDefault(candidate => candidate == "smartctl.exe" || File.Exists(candidate));
    }

    private static List<SmartDevice> ScanDevices(string smartctl)
    {
        using var json = RunJson(smartctl, "--scan-open", "-j");
        if (json is null || !json.RootElement.TryGetProperty("devices", out var devices)) return [];
        return devices.EnumerateArray().Select(item => new SmartDevice(
            item.GetProperty("name").GetString() ?? "",
            item.TryGetProperty("type", out var type) ? type.GetString() : null)).ToList();
    }

    private static List<SensorReading> ReadSmartTemperatures(string smartctl, SmartDevice device)
    {
        var args = new List<string> { "-a", "-j" };
        if (!string.IsNullOrWhiteSpace(device.Type)) args.AddRange(["-d", device.Type!]);
        args.Add(device.Name);
        using var json = RunJson(smartctl, args.ToArray());
        if (json is null) return [];
        var root = json.RootElement;
        var values = new List<(string Name, double Value)>();
        if (root.TryGetProperty("temperature", out var temperature) && temperature.TryGetProperty("current", out var current))
            Add(values, "综合", current);
        if (root.TryGetProperty("nvme_smart_health_information_log", out var nvme))
        {
            if (nvme.TryGetProperty("temperature", out var composite)) Add(values, "综合", composite);
            if (nvme.TryGetProperty("temperature_sensors", out var temperatureSensors))
            {
                var number = 1;
                foreach (var value in temperatureSensors.EnumerateArray()) Add(values, $"传感器 {number++}", value);
            }
        }
        return values
            .Where(item => item.Value > 0 && item.Value <= 150 && double.IsFinite(item.Value))
            .GroupBy(item => $"{item.Name}:{item.Value}")
            .Select((group, index) => new SensorReading($"smart:{index}", group.First().Name, Math.Round(group.First().Value, 1), "smartctl"))
            .ToList();
    }

    private static void Add(List<(string Name, double Value)> values, string name, JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Number && element.TryGetDouble(out var value)) values.Add((name, value));
    }

    private static JsonDocument? RunJson(string executable, params string[] args)
    {
        try
        {
            var start = new ProcessStartInfo(executable) { RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true };
            foreach (var arg in args) start.ArgumentList.Add(arg);
            using var process = Process.Start(start);
            if (process is null) return null;
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(5000);
            return string.IsNullOrWhiteSpace(output) ? null : JsonDocument.Parse(output);
        }
        catch { return null; }
    }

    private static int? DeviceIndex(string name)
    {
        if (!name.StartsWith("/dev/sd", StringComparison.OrdinalIgnoreCase) || name.Length < 8) return null;
        var letter = char.ToLowerInvariant(name[7]);
        return letter is >= 'a' and <= 'z' ? letter - 'a' : null;
    }

    private sealed record SmartDevice(string Name, string? Type);
}

static class AtomicJson
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true, PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    public static void Write(string output, object value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var temporary = output + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(value, Options));
        File.Move(temporary, output, true);
    }
}

sealed record SensorReading(string Id, string Name, double Value, string Source);
sealed record DeviceReading(string Id, string Kind, string Name, List<string> DriveLetters, double? Temperature, List<SensorReading> Sensors);
