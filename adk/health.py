"""«Здоровье ПК»: аптайм, ОЗУ/CPU, логические диски, S.M.A.R.T. физических дисков и (по запросу) карта диска.

Работает только с Windows-хоста (``Get-CimInstance -ComputerName``, WinRM/DCOM, для карты диска — общий ресурс
``\\\\host\\C$``; при закрытом WinRM тот же опрос идёт по DCOM/WMI — даты в этом случае приходят строками DMTF
и переводятся в нормальный вид функцией ``FmtDate`` внутри скрипта). Карта диска с 3.7.0 считается в 8 потоков
(runspace pool): папки верхнего уровня и профили пользователей обходятся одновременно. На других ОС и при
недоступности ПК возвращается структура с ``error``. Весь разбор вынесен
в чистые функции (:func:`parse_health_json`, :func:`parse_smart_vendor`, :func:`disk_verdict`,
:func:`parse_usage_json`, :func:`squarify`), поэтому тестируется без PowerShell.

Что откуда берётся
------------------
* ``Win32_OperatingSystem`` / ``Win32_LogicalDisk`` / ``Win32_Processor`` — аптайм, ОЗУ, CPU, разделы.
* ``MSFT_PhysicalDisk`` + ``MSFT_StorageReliabilityCounter`` (``root/Microsoft/Windows/Storage``) — модель,
  тип носителя (SSD/HDD/NVMe), статус Windows, температура, износ, наработка, ошибки чтения/записи.
* ``MSStorageDriver_FailurePredictStatus`` / ``…FailurePredictData`` (``root/wmi``) — флаг «предсказан отказ»
  и сырые 512 байт S.M.A.R.T. (SATA), которые разбираются здесь так же, как это делает CrystalDiskInfo.
"""
from __future__ import annotations

import base64
import json
import logging
import ntpath
from datetime import datetime, timedelta

from .netutils import is_valid_hostname

log = logging.getLogger(__name__)

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$c = '__HOST__'

# Даты приходит и от CIM (DateTime), и от WMI (строка DMTF '20250101120000.500000+180').
# Прямой вызов .ToString('формат') на строке падает «Не удается найти перегрузку для "ToString"…» —
# именно так обнулялись «Обзор» и «S.M.A.R.T.» на ПК, где WinRM закрыт и работает только DCOM/WMI.
function FmtDate($v) {
    if ($null -eq $v) { return '' }
    if ($v -is [datetime]) { return $v.ToString('yyyy-MM-dd HH:mm:ss') }
    $s = [string]$v
    if ($s -match '^\d{14}') {
        return $s.Substring(0,4) + '-' + $s.Substring(4,2) + '-' + $s.Substring(6,2) + ' ' +
               $s.Substring(8,2) + ':' + $s.Substring(10,2) + ':' + $s.Substring(12,2)
    }
    return $s
}

# 1. Операционная система, логические диски, процессор
$os = $null
$why = @()
try { $os = Get-CimInstance Win32_OperatingSystem -ComputerName $c -ErrorAction Stop } catch {
    $why += ('WinRM: ' + $_.Exception.Message)
    try { $os = Get-WmiObject Win32_OperatingSystem -ComputerName $c -ErrorAction Stop } catch { $why += ('DCOM: ' + $_.Exception.Message) }
}
if (-not $os) {
    throw ("ПК $c не отвечает по CIM/WMI. " + ($why -join ' | '))
}

$disks = @()
try {
    $disks = Get-CimInstance Win32_LogicalDisk -ComputerName $c -Filter "DriveType=3" -ErrorAction Stop |
        Select-Object DeviceID, Size, FreeSpace, VolumeName, FileSystem
} catch {
    try {
        $disks = Get-WmiObject Win32_LogicalDisk -ComputerName $c -Filter "DriveType=3" -ErrorAction Stop |
            Select-Object DeviceID, Size, FreeSpace, VolumeName, FileSystem
    } catch {}
}

$cpu = $null
try {
    $cpu = (Get-CimInstance Win32_Processor -ComputerName $c -ErrorAction Stop | Measure-Object -Property LoadPercentage -Average).Average
} catch {
    try {
        $cpu = (Get-WmiObject Win32_Processor -ComputerName $c -ErrorAction Stop | Measure-Object -Property LoadPercentage -Average).Average
    } catch {}
}

# 2. Физические диски и S.M.A.R.T. (MSFT_PhysicalDisk + Win32_DiskDrive + root/wmi)
$phys = @()
$rc = @{}
$pred = @{}
$raw = @{}
$thresh = @{}
$drives = @{}

# Опрос WMI root/wmi для S.M.A.R.T. (PredictFailure, VendorSpecific Raw Data, Thresholds)
try {
    Get-CimInstance -Namespace 'root/wmi' -ClassName MSStorageDriver_FailurePredictStatus -ComputerName $c -ErrorAction Stop |
        ForEach-Object { $pred[$_.InstanceName] = [bool]$_.PredictFailure }
} catch {
    try {
        Get-WmiObject -Namespace 'root/wmi' -Class MSStorageDriver_FailurePredictStatus -ComputerName $c -ErrorAction Stop |
            ForEach-Object { $pred[$_.InstanceName] = [bool]$_.PredictFailure }
    } catch {}
}

try {
    Get-CimInstance -Namespace 'root/wmi' -ClassName MSStorageDriver_FailurePredictData -ComputerName $c -ErrorAction Stop |
        ForEach-Object { $raw[$_.InstanceName] = [Convert]::ToBase64String([byte[]]$_.VendorSpecific) }
} catch {
    try {
        Get-WmiObject -Namespace 'root/wmi' -Class MSStorageDriver_FailurePredictData -ComputerName $c -ErrorAction Stop |
            ForEach-Object { $raw[$_.InstanceName] = [Convert]::ToBase64String([byte[]]$_.VendorSpecific) }
    } catch {}
}

try {
    Get-CimInstance -Namespace 'root/wmi' -ClassName MSStorageDriver_FailurePredictThresholds -ComputerName $c -ErrorAction Stop |
        ForEach-Object { $thresh[$_.InstanceName] = [Convert]::ToBase64String([byte[]]$_.VendorSpecific) }
} catch {
    try {
        Get-WmiObject -Namespace 'root/wmi' -Class MSStorageDriver_FailurePredictThresholds -ComputerName $c -ErrorAction Stop |
            ForEach-Object { $thresh[$_.InstanceName] = [Convert]::ToBase64String([byte[]]$_.VendorSpecific) }
    } catch {}
}

# Опрос Win32_DiskDrive
try {
    Get-CimInstance Win32_DiskDrive -ComputerName $c -ErrorAction Stop | ForEach-Object { $drives[[string]$_.Index] = $_ }
} catch {
    try {
        Get-WmiObject Win32_DiskDrive -ComputerName $c -ErrorAction Stop | ForEach-Object { $drives[[string]$_.Index] = $_ }
    } catch {}
}

# Попытка через root/Microsoft/Windows/Storage
try {
    $ns = 'root/Microsoft/Windows/Storage'
    $pd = Get-CimInstance -Namespace $ns -ClassName MSFT_PhysicalDisk -ComputerName $c -ErrorAction Stop
    try {
        Get-CimInstance -Namespace $ns -ClassName MSFT_StorageReliabilityCounter -ComputerName $c -ErrorAction Stop |
            ForEach-Object { $rc[$_.DeviceId] = $_ }
    } catch {}

    if ($pd) {
        $phys = @($pd | ForEach-Object {
            $r = $rc[$_.DeviceId]
            $d = $drives[[string]$_.DeviceId]
            $pn = if ($d) { $d.PNPDeviceID } else { '' }
            $pk = $null
            if ($pn) { $pk = ($pred.Keys | Where-Object { $_ -like "$pn*" } | Select-Object -First 1) }
            if (-not $pk -and $raw.Count -gt 0) {
                $idx = $_.DeviceId
                $pk = ($raw.Keys | Where-Object { $_ -like "*$idx*" } | Select-Object -First 1)
            }
            @{
                id = [string]$_.DeviceId; model = [string]$_.FriendlyName; serial = [string]$_.SerialNumber
                fw = [string]$_.FirmwareVersion; media = [int]$_.MediaType; bus = [int]$_.BusType
                size = [int64]$_.Size; health = [int]$_.HealthStatus; usage = [int]$_.Usage
                temp = if ($r) { $r.Temperature } else { $null }
                temp_max = if ($r) { $r.TemperatureMax } else { $null }
                wear = if ($r) { $r.Wear } else { $null }
                hours = if ($r) { $r.PowerOnHours } else { $null }
                read_err = if ($r) { $r.ReadErrorsUncorrected } else { $null }
                write_err = if ($r) { $r.WriteErrorsUncorrected } else { $null }
                predict = if ($pk) { $pred[$pk] } else { $null }
                smart = if ($pk -and $raw.ContainsKey($pk)) { $raw[$pk] } else { $null }
                thresholds = if ($pk -and $thresh.ContainsKey($pk)) { $thresh[$pk] } else { $null }
            }
        })
    }
} catch {}

# Если MSFT_PhysicalDisk не отдал диски — используем Win32_DiskDrive
if ($phys.Count -eq 0 -and $drives.Count -gt 0) {
    $phys = @($drives.Values | ForEach-Object {
        $pn = $_.PNPDeviceID
        $pk = $null
        if ($pn) { $pk = ($pred.Keys | Where-Object { $_ -like "$pn*" } | Select-Object -First 1) }
        if (-not $pk -and $raw.Count -gt 0) {
            $idx = $_.Index
            $pk = ($raw.Keys | Where-Object { $_ -like "*$idx*" } | Select-Object -First 1)
        }
        if (-not $pk -and $raw.Count -eq 1) {
            $pk = ($raw.Keys | Select-Object -First 1)
        }
        $bus = 0
        $iface = [string]$_.InterfaceType
        if ($iface -match 'IDE|ATA') { $bus = 3 }
        elseif ($iface -match 'SCSI') { $bus = 1 }
        elseif ($iface -match 'USB') { $bus = 7 }
        elseif ($iface -match 'NVMe') { $bus = 17 }

        $media = 0
        $modelStr = [string]$_.Model
        if ($modelStr -match 'SSD|NVMe|Solid State') { $media = 4 }

        $statusStr = [string]$_.Status
        $healthVal = if ($statusStr -eq 'OK') { 0 } elseif ($statusStr -match 'Degraded|Pred Fail') { 1 } else { 2 }

        @{
            id = [string]$_.Index
            model = if ($modelStr) { $modelStr } else { [string]$_.Caption }
            serial = [string]$_.SerialNumber
            fw = [string]$_.FirmwareRevision
            media = $media
            bus = $bus
            size = [int64]$_.Size
            health = $healthVal
            usage = 1
            temp = $null
            temp_max = $null
            wear = $null
            hours = $null
            read_err = $null
            write_err = $null
            predict = if ($pk) { $pred[$pk] } else { $null }
            smart = if ($pk -and $raw.ContainsKey($pk)) { $raw[$pk] } else { $null }
            thresholds = if ($pk -and $thresh.ContainsKey($pk)) { $thresh[$pk] } else { $null }
        }
    })
}

$nowStr = FmtDate $os.LocalDateTime
if (-not $nowStr) { $nowStr = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') }

[pscustomobject]@{
    boot = FmtDate $os.LastBootUpTime
    now = $nowStr
    total_mb = [int]($os.TotalVisibleMemorySize / 1024)
    free_mb = [int]($os.FreePhysicalMemory / 1024)
    cpu = $cpu
    os = $os.Caption
    disks = @($disks | ForEach-Object { @{ id = $_.DeviceID; size = [int64]$_.Size; free = [int64]$_.FreeSpace; label = $_.VolumeName; fs = $_.FileSystem } })
    phys = $phys
} | ConvertTo-Json -Compress -Depth 5
"""

# Карта диска (WinDirStat-style): размеры папок верхнего уровня, самые большие файлы и «известные пожиратели места».
# Оптимизированный однопроходный обход с защитой от циклических ссылок и зависаний на reparse points.
_PS_USAGE = r"""
$ErrorActionPreference = 'SilentlyContinue'
$root = '__ROOT__'
$top = __TOP__
if (-not [IO.Directory]::Exists($root)) {
    $ErrorActionPreference = 'Stop'
    throw "Ресурс $root недоступен: ПК выключен, административные общие ресурсы (C$) отключены или нет прав администратора"
}

# 3.7.0: обход в 8 потоков (runspace pool) — папки верхнего уровня и профили Users считаются одновременно,
# а не по очереди. «Известные пожиратели» (Temp профилей, кэш установщика, SoftwareDistribution и т.д.)
# считаются тем же проходом: вместо второго обхода поддерево один раз суммируется и попадает и в общий
# размер, и в список «что можно почистить».
$rootLow = $root.ToLowerInvariant()
$hogExact = @{}
foreach ($k in @(
    @{ p = 'Windows\SoftwareDistribution\Download'; l = 'Обновления Windows' },
    @{ p = 'Windows\Temp';                            l = 'Временные файлы Windows' },
    @{ p = 'Windows.old';                             l = 'Старая Windows' },
    @{ p = '$WinREAgent';                             l = 'Остатки обновления ($WinREAgent)' },
    @{ p = 'Windows\Installer';                       l = 'Кэш установщика' },
    @{ p = 'ProgramData\Package Cache';               l = 'Кэш пакетов (ProgramData)' },
    @{ p = 'Windows\Logs\CBS';                        l = 'Журналы CBS' },
    @{ p = 'Windows\Minidump';                        l = 'Дампы памяти' },
    @{ p = 'Windows\LiveKernelReports';               l = 'Отчёты ядра' },
    @{ p = 'Windows\Prefetch';                        l = 'Предзагрузка (Prefetch)' },
    @{ p = '$Recycle.Bin';                            l = 'Корзина' }
)) { $hogExact[($rootLow + '\' + $k.p.ToLowerInvariant())] = $k.l }

$scanBody = @'
param($base, $rootLow, $hogExact)
function Walk-Tree([string]$base, [string]$rootLow, $hogExact) {
    $sum = [int64]0; $count = 0; $errors = 0
    $big = New-Object System.Collections.Generic.List[object]
    $hogs = New-Object System.Collections.Generic.List[object]
    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($base)
    while ($stack.Count -gt 0) {
        $cur = $stack.Pop()
        try {
            foreach ($f in [IO.Directory]::EnumerateFiles($cur)) {
                try {
                    $len = ([IO.FileInfo]$f).Length
                    $sum += $len; $count++
                    if ($len -ge 50MB) { $big.Add(@{ path = $f; size = $len }) }
                } catch { $errors++ }
            }
        } catch { $errors++ }
        try {
            foreach ($s in [IO.Directory]::EnumerateDirectories($cur)) {
                try {
                    $attr = [IO.File]::GetAttributes($s)
                    if ($attr -band [IO.FileAttributes]::ReparsePoint) { continue }
                    $low = $s.ToLowerInvariant()
                    $hogLabel = $hogExact[$low]
                    if (-not $hogLabel -and $low -like '*\appdata\local\temp') {
                        $prof = Split-Path (Split-Path (Split-Path $s -Parent) -Parent) -Leaf
                        $hogLabel = 'Temp профиля ' + $prof
                    }
                    if ($hogLabel) {
                        $r = Walk-Tree $s $rootLow $hogExact     # поддерево считается один раз: и в сумму, и в «почистить»
                        $sum += [int64]$r.sum; $count += [int]$r.count; $errors += [int]$r.errors
                        foreach ($b in @($r.big)) { $big.Add($b) }
                        if ([int64]$r.sum -gt 0) { $hogs.Add(@{ label = $hogLabel; path = $s; size = [int64]$r.sum; files = [int]$r.count }) }
                        continue
                    }
                    $stack.Push($s)
                } catch { $errors++ }
            }
        } catch { $errors++ }
    }
    @{ sum = $sum; count = $count; errors = $errors; big = $big; hogs = $hogs }
}
try { Walk-Tree $base $rootLow $hogExact } catch { @{ sum = [int64]0; count = 0; errors = 1; big = @(); hogs = @() } }
'@

# Задачи: папки верхнего уровня; Users разворачиваем в профили — каждый профиль отдельным потоком
$jobs = New-Object System.Collections.Generic.List[object]
foreach ($d in [IO.Directory]::EnumerateDirectories($root)) {
    try {
        $attr = [IO.File]::GetAttributes($d)
        if ($attr -band [IO.FileAttributes]::ReparsePoint) { continue }
    } catch { continue }
    if ((Split-Path $d -Leaf) -ieq 'Users') {
        foreach ($u in [IO.Directory]::EnumerateDirectories($d)) {
            try {
                $ua = [IO.File]::GetAttributes($u)
                if ($ua -band [IO.FileAttributes]::ReparsePoint) { continue }
            } catch { continue }
            $jobs.Add(@{ path = $u; isUser = $true })
        }
    } else {
        $jobs.Add(@{ path = $d; isUser = $false })
    }
}

$iss = [System.Management.Automation.Runspaces.InitialSessionState]::CreateDefault()
$pool = [runspacefactory]::CreateRunspacePool(1, 8, $iss, $null)
$pool.Open()
$runners = @()
foreach ($j in $jobs) {
    $ps = [powershell]::Create()
    $ps.RunspacePool = $pool
    [void]$ps.AddScript($scanBody).AddArgument($j.path).AddArgument($rootLow).AddArgument($hogExact)
    $runners += @{ ps = $ps; handle = $ps.BeginInvoke(); job = $j }
}

$dirs = @(); $files = New-Object System.Collections.Generic.List[object]
$hogsMap = @{}; $userMap = @{}
$errors = 0; $totalFiles = 0
foreach ($r in $runners) {
    $o = $null
    try { $o = @($r.ps.EndInvoke($r.handle))[0] } catch { $errors++; continue }
    try { $r.ps.Dispose() } catch {}
    if (-not $o) { continue }
    $errors += [int]$o.errors
    $totalFiles += [int]$o.count
    if ($r.job.isUser) {
        $userMap[$r.job.path] = @{ path = $r.job.path; size = [int64]$o.sum; files = [int]$o.count }
    } else {
        $dirs += @{ path = $r.job.path; size = [int64]$o.sum; files = [int]$o.count }
    }
    foreach ($b in @($o.big)) { try { $files.Add(@{ path = [string]$b.path; size = [int64]$b.size }) } catch {} }
    foreach ($h in @($o.hogs)) { try { $hogsMap[[string]$h.label] = @{ label = [string]$h.label; path = [string]$h.path; size = [int64]$h.size; files = [int]$h.files } } catch {} }
}
try { $pool.Close() } catch {}

if ($userMap.Count -gt 0) {
    $uTotal = [int64]0; $uCount = 0
    foreach ($u in $userMap.Values) { $uTotal += [int64]$u.size; $uCount += [int]$u.files }
    $dirs += @{ path = ($root + '\Users'); size = $uTotal; files = $uCount }
}

# Корневые файлы (hiberfil.sys, pagefile.sys, swapfile.sys и т.д.)
$rootFiles = [int64]0
try {
    foreach ($f in [IO.Directory]::EnumerateFiles($root)) {
        try {
            $fi = [IO.FileInfo]$f
            $len = $fi.Length
            $rootFiles += $len
            $leaf = $fi.Name
            if ($leaf -eq 'hiberfil.sys') { $hogsMap['Файл гибернации'] = @{ label = 'Файл гибернации'; path = $f; size = $len; files = 1 } }
            elseif ($leaf -eq 'pagefile.sys') { $hogsMap['Файл подкачки'] = @{ label = 'Файл подкачки'; path = $f; size = $len; files = 1 } }
            elseif ($leaf -eq 'swapfile.sys') { $hogsMap['Файл подкачки (swap)'] = @{ label = 'Файл подкачки (swap)'; path = $f; size = $len; files = 1 } }
            if ($len -ge 50MB) { $files.Add(@{ path = $f; size = $len }) }
        } catch {}
    }
} catch {}

$hogs = @($hogsMap.Values)
$users = @($userMap.Values)
$big = @($files | Sort-Object { $_.size } -Descending | Select-Object -First $top)

[pscustomobject]@{
    root = $root; dirs = $dirs; root_files = $rootFiles; files = $big; hogs = $hogs; users = $users
    total_files = $totalFiles; errors = $errors
} | ConvertTo-Json -Compress -Depth 5
"""


LOW_DISK_GB = 10.0
LOW_DISK_PCT = 10.0
LONG_UPTIME_DAYS = 30
HOT_DISK_C = 55          # как у CrystalDiskInfo: ≥ 55 °C — жёлтая температура
SSD_WEAR_WARN = 80       # износ ≥ 80 % — «Осторожно», 95+ — «Плохо»
HOURS_WARN = 40000       # > ~4,5 лет наработки — просто подсветка, не порог

MEDIA = {0: "Неизвестно", 3: "HDD", 4: "SSD", 5: "SCM"}
BUS = {0: "?", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394", 5: "SSA", 6: "FC", 7: "USB", 8: "RAID", 9: "iSCSI",
       10: "SAS", 11: "SATA", 12: "SD", 13: "MMC", 15: "File", 16: "Virtual", 17: "NVMe"}

# Атрибуты S.M.A.R.T. — те, что имеют смысл для вердикта и понятны инженеру (ID → (имя, критичен?)).
SMART_ATTRS = {
    0x01: ("Ошибки чтения (Raw Read Error Rate)", False), 0x03: ("Время раскрутки (Spin-Up Time)", False),
    0x04: ("Циклы старт/стоп", False), 0x05: ("Переназначенные секторы (Reallocated)", True),
    0x07: ("Ошибки позиционирования", False), 0x09: ("Наработка, ч (Power-On Hours)", False),
    0x0A: ("Повторы раскрутки (Spin Retry)", True), 0x0C: ("Включения питания", False),
    0xAB: ("Program Fail Count", False), 0xAC: ("Erase Fail Count", False), 0xAD: ("Wear Leveling Count", False),
    0xB1: ("Wear Range Delta", False), 0xB3: ("Used Reserved Block Count", False), 0xB5: ("Program Fail (total)", False),
    0xB6: ("Erase Fail (total)", False), 0xB7: ("Runtime Bad Block", True), 0xB8: ("End-to-End Error", True),
    0xBB: ("Неисправимые ошибки (Reported Uncorrectable)", True), 0xBC: ("Command Timeout", False),
    0xBD: ("High Fly Writes", False), 0xBE: ("Температура воздуха (Airflow)", False), 0xBF: ("G-Sense Error Rate", False),
    0xC0: ("Аварийные отключения", False), 0xC1: ("Циклы парковки", False), 0xC2: ("Температура", False),
    0xC3: ("Hardware ECC Recovered", False), 0xC4: ("Событий переназначения", True),
    0xC5: ("Секторы-кандидаты (Current Pending)", True), 0xC6: ("Неисправимые секторы (Offline Uncorrectable)", True),
    0xC7: ("Ошибки CRC интерфейса (UDMA CRC)", False), 0xC8: ("Write Error Rate", False), 0xE7: ("Остаток ресурса SSD, %", False),
    0xE8: ("Доступный резерв, %", False), 0xE9: ("Записано на NAND / Media Wearout", False),
    0xF1: ("Всего записано (LBA)", False), 0xF2: ("Всего прочитано (LBA)", False),
}
_CRITICAL_RAW = {0x05, 0xC4, 0xC5, 0xC6, 0xBB, 0xB7, 0xB8, 0x0A}


# --------------------------------------------------------------------------- разбор
def parse_smart_vendor(data: bytes | str | None, thresh_data: bytes | str | None = None) -> list[dict]:
    """512 байт VendorSpecific (или их base64) → [{id, name, current, worst, threshold, raw, critical}].

    Формат ATA: первые 2 байта — версия структуры, затем до 30 записей по 12 байт:
    ``id, flags(2), current, worst, raw(6), reserved``. Записи с id == 0 — пустые.
    Пороги (thresholds): структура по 12 байт: ``id, threshold, reserved(10)``.
    """
    if not data:
        return []
    if isinstance(data, str):
        try:
            data = base64.b64decode(data)
        except (ValueError, TypeError):
            return []

    thresh_by_id: dict[int, int] = {}
    if thresh_data:
        if isinstance(thresh_data, str):
            try:
                thresh_data = base64.b64decode(thresh_data)
            except (ValueError, TypeError):
                thresh_data = None
        if thresh_data and len(thresh_data) >= 2:
            for toff in range(2, min(len(thresh_data), 2 + 30 * 12), 12):
                trec = thresh_data[toff:toff + 12]
                if len(trec) < 2 or trec[0] == 0:
                    continue
                thresh_by_id[trec[0]] = trec[1]

    out: list[dict] = []
    for off in range(2, min(len(data), 2 + 30 * 12), 12):
        rec = data[off:off + 12]
        if len(rec) < 12 or rec[0] == 0:
            continue
        aid = rec[0]
        raw = int.from_bytes(rec[5:11], "little")
        if aid in (0xC2, 0xBE):  # температура: младший байт — текущая, остальное — min/max
            raw = rec[5]
        name, critical = SMART_ATTRS.get(aid, (f"Атрибут {aid:02X}", False))
        threshold = thresh_by_id.get(aid)
        out.append({"id": aid, "name": name, "current": rec[3], "worst": rec[4],
                    "threshold": threshold, "raw": raw, "critical": critical})
    return out


def disk_verdict(p: dict) -> tuple[str, list[str]]:
    """Вердикт по диску в терминах CrystalDiskInfo: ('good'|'caution'|'bad'|'unknown', причины)."""
    reasons: list[str] = []
    level = 0  # 0 good, 1 caution, 2 bad
    if p.get("predict"):
        reasons.append("контроллер предсказывает отказ (S.M.A.R.T. PredictFailure)")
        level = 2
    if p.get("health") == 2:
        reasons.append("Windows: состояние «Неисправен»")
        level = 2
    elif p.get("health") == 1:
        reasons.append("Windows: состояние «Предупреждение»")
        level = max(level, 1)
    for a in p.get("attrs") or []:
        if a["id"] in _CRITICAL_RAW and a["raw"] > 0:
            reasons.append(f"{a['name']}: {a['raw']}")
            level = max(level, 2 if a["id"] in (0xC6, 0xBB) and a["raw"] > 10 else 1)
        if a.get("threshold") is not None and a["threshold"] > 0 and a["current"] <= a["threshold"]:
            reasons.append(f"{a['name']}: значение ниже порога")
            level = 2
    for key in ("read_err", "write_err"):
        if p.get(key):
            reasons.append(f"{'неисправимые ошибки чтения' if key == 'read_err' else 'неисправимые ошибки записи'}: {p[key]}")
            level = max(level, 1)
    wear = p.get("wear")
    if wear is not None and wear >= 95:
        reasons.append(f"ресурс SSD исчерпан на {wear}%")
        level = 2
    elif wear is not None and wear >= SSD_WEAR_WARN:
        reasons.append(f"износ SSD {wear}%")
        level = max(level, 1)
    t = p.get("temp")
    if t is not None and t >= HOT_DISK_C:
        reasons.append(f"температура {t} °C")
        level = max(level, 1)
    known = any(p.get(k) is not None for k in ("predict", "health", "wear", "temp")) or bool(p.get("attrs"))
    if not known and not reasons:
        return "unknown", []
    return ("good", "caution", "bad")[level], reasons


VERDICT_LABEL = {"good": "Хорошо", "caution": "Осторожно", "bad": "Плохо", "unknown": "Нет данных"}


def _phys_disk(x: dict) -> dict:
    size = float(x.get("size") or 0)
    p = {"id": str(x.get("id", "?")), "model": (x.get("model") or "Диск").strip(), "serial": (x.get("serial") or "").strip(),
         "fw": (x.get("fw") or "").strip(), "media": MEDIA.get(int(x.get("media") or 0), "Неизвестно"),
         "bus": BUS.get(int(x.get("bus") or 0), "?"), "size_gb": round(size / 1000 ** 3), "health": x.get("health"),
         "temp": x.get("temp"), "temp_max": x.get("temp_max"), "wear": x.get("wear"), "hours": x.get("hours"),
         "read_err": x.get("read_err"), "write_err": x.get("write_err"), "predict": x.get("predict"),
         "attrs": parse_smart_vendor(x.get("smart"), x.get("thresholds"))}
    if p["bus"] == "NVMe" and p["media"] == "Неизвестно":
        p["media"] = "SSD"
    # если контроллер не отдал счётчики — попробуем вытащить из сырых атрибутов
    by_id = {a["id"]: a for a in p["attrs"]}
    if p["temp"] is None and (0xC2 in by_id or 0xBE in by_id):
        p["temp"] = (by_id.get(0xC2) or by_id[0xBE])["raw"]
    if p["hours"] is None and 0x09 in by_id:
        p["hours"] = by_id[0x09]["raw"]
    if p["wear"] is None and 0xE7 in by_id:
        p["wear"] = max(0, 100 - by_id[0xE7]["current"])
    if p["wear"] is None and 0xE9 in by_id:
        p["wear"] = max(0, 100 - by_id[0xE9]["current"])
    for k in ("temp", "temp_max", "wear", "hours", "read_err", "write_err"):
        if p[k] is not None:
            try:
                p[k] = int(float(p[k]))
            except (TypeError, ValueError):
                p[k] = None
    p["verdict"], p["reasons"] = disk_verdict(p)
    return p


def parse_health_json(text: str) -> dict:
    """JSON от PowerShell → {uptime_days, boot, ram_used_pct, cpu, disks:[…], phys:[…], warnings, score}."""
    d = json.loads(text)
    if not d.get("boot"):
        return {"error": "ПК не вернул дату последней загрузки (LastBootUpTime пуст) — данные «Обзора» неполные"}
    try:
        boot = datetime.strptime(d["boot"][:19], "%Y-%m-%d %H:%M:%S")
        now = datetime.strptime((d.get("now") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"error": f"ПК вернул дату загрузки в неожиданном виде: {d['boot']!r}"}
    uptime: timedelta = now - boot
    total, free = float(d.get("total_mb") or 0), float(d.get("free_mb") or 0)
    ram_pct = round((total - free) / total * 100) if total else 0
    disks, warnings = [], []
    for x in d.get("disks") or []:
        size, fr = float(x.get("size") or 0), float(x.get("free") or 0)
        gb, fgb = size / 1024 ** 3, fr / 1024 ** 3
        pct = fr / size * 100 if size else 0
        low = size > 0 and (fgb < LOW_DISK_GB or pct < LOW_DISK_PCT)
        disks.append({"id": x.get("id", "?"), "label": x.get("label") or "", "fs": x.get("fs") or "",
                      "total_gb": round(gb, 1), "free_gb": round(fgb, 1), "free_pct": round(pct), "low": low})
        if low:
            warnings.append(f"мало места на {x.get('id', '?')} ({fgb:.1f} ГБ, {pct:.0f}%)")
    phys = [_phys_disk(x) for x in (d.get("phys") or [])]
    for p in phys:
        if p["verdict"] == "bad":
            warnings.append(f"диск {p['model']}: ПЛОХО — {'; '.join(p['reasons'])}")
        elif p["verdict"] == "caution":
            warnings.append(f"диск {p['model']}: осторожно — {'; '.join(p['reasons'])}")
    if uptime.days >= LONG_UPTIME_DAYS:
        warnings.append(f"не перезагружался {uptime.days} дн.")
    if ram_pct >= 90:
        warnings.append(f"ОЗУ занято на {ram_pct}%")
    cpu = d.get("cpu")
    if cpu is not None and float(cpu) >= 90:
        warnings.append(f"CPU {float(cpu):.0f}%")
    score = "bad" if any(p["verdict"] == "bad" for p in phys) else "caution" if warnings else "good"
    return {"uptime_days": uptime.days, "uptime": _fmt_td(uptime), "boot": d["boot"], "os": d.get("os", ""),
            "ram_total_gb": round(total / 1024, 1), "ram_used_pct": ram_pct, "cpu": None if cpu is None else round(float(cpu)),
            "disks": disks, "phys": phys, "warnings": warnings, "score": score}


def _fmt_td(td: timedelta) -> str:
    h = td.seconds // 3600
    return f"{td.days} дн. {h} ч." if td.days else f"{h} ч. {(td.seconds % 3600) // 60} мин."


def fmt_size(n: float | int | None) -> str:
    n = float(n or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def fmt_hours(h: int | None) -> str:
    if h is None:
        return "—"
    days = h // 24
    return f"{h} ч." + (f" (~{days // 365} г. {days % 365 // 30} мес.)" if days >= 365 else f" (~{days} дн.)" if days else "")


def format_health(h: dict) -> str:
    """Текстовый отчёт (для копирования в буфер, CLI и уведомлений)."""
    if "error" in h:
        return f"⚠️ {h['error']}"
    lines = [f"⏱ Аптайм: {h['uptime']} (загружен {h['boot']})",
             f"🧠 ОЗУ: {h['ram_used_pct']}% из {h['ram_total_gb']} ГБ" + (f" · CPU {h['cpu']}%" if h.get("cpu") is not None else "")]
    for d in h["disks"]:
        flag = "🔴" if d["low"] else "🟢"
        lines.append(f"{flag} {d['id']} свободно {d['free_gb']} ГБ из {d['total_gb']} ГБ ({d['free_pct']}%)")
    for p in h.get("phys") or []:
        icon = {"good": "🟢", "caution": "🟡", "bad": "🔴"}.get(p["verdict"], "⚪")
        extra = " · ".join(x for x in (f"{p['temp']} °C" if p.get("temp") is not None else "",
                                       f"наработка {p['hours']} ч." if p.get("hours") is not None else "",
                                       f"износ {p['wear']}%" if p.get("wear") is not None else "") if x)
        lines.append(f"{icon} {p['model']} ({p['media']}, {p['size_gb']} ГБ): {VERDICT_LABEL[p['verdict']]}" + (f" — {extra}" if extra else ""))
    if h["warnings"]:
        lines.append("⚠️ " + "; ".join(h["warnings"]))
    else:
        lines.append("✅ Замечаний нет")
    return "\n".join(lines)


# --------------------------------------------------------------------------- карта диска
def parse_usage_json(text: str) -> dict:
    """JSON скрипта карты диска → {root, total, dirs:[{name,path,size,files,pct}], files, hogs, users, errors}."""
    d = json.loads(text)
    dirs = [{"name": ntpath.basename(x["path"].rstrip("\\/")) or x["path"], "path": x["path"], "size": int(x.get("size") or 0),
             "files": int(x.get("files") or 0)} for x in (d.get("dirs") or [])]
    root_files = int(d.get("root_files") or 0)
    if root_files:
        dirs.append({"name": "<файлы в корне>", "path": d.get("root", ""), "size": root_files, "files": 0})
    total = sum(x["size"] for x in dirs)
    for x in dirs:
        x["pct"] = round(x["size"] / total * 100, 1) if total else 0.0
    dirs.sort(key=lambda x: -x["size"])
    files = sorted(({"path": f["path"], "size": int(f.get("size") or 0)} for f in (d.get("files") or [])), key=lambda f: -f["size"])
    # «что можно почистить» — из того же обхода, что и карта: только реально найденные на этом томе пути
    hogs = sorted(({"label": h["label"], "path": h["path"], "size": int(h.get("size") or 0), "files": int(h.get("files") or 0)}
                   for h in (d.get("hogs") or []) if int(h.get("size") or 0) > 0), key=lambda h: -h["size"])
    users = sorted(({"name": ntpath.basename(u["path"].rstrip("\\/")), "path": u["path"], "size": int(u.get("size") or 0)}
                    for u in (d.get("users") or [])), key=lambda u: -u["size"])
    return {"root": d.get("root", ""), "total": total, "dirs": dirs, "files": files, "hogs": hogs, "users": users,
            "hogs_total": sum(h["size"] for h in hogs),
            "total_files": int(d.get("total_files") or 0), "errors": int(d.get("errors") or 0)}


def squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap (Bruls et al.): доли ``values`` → прямоугольники (x, y, w, h) внутри заданной области.

    Значения должны быть отсортированы по убыванию; нулевые дают прямоугольники нулевой площади.
    """
    total = float(sum(values))
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0.0, 0.0) for _ in values]
    scale = w * h / total
    areas = [v * scale for v in values]
    rects: list[tuple[float, float, float, float]] = []

    def worst(row: list[float], side: float) -> float:
        s = sum(row)
        if s <= 0 or side <= 0:
            return float("inf")
        mx, mn = max(row), min(row)
        return max(side * side * mx / (s * s), s * s / (side * side * mn)) if mn > 0 else float("inf")

    def layout(row: list[float], rx, ry, rw, rh):
        s = sum(row)
        nonlocal_rects = []
        if rw >= rh:  # столбец слева
            cw = s / rh if rh else 0
            cy = ry
            for a in row:
                ch = a / cw if cw else 0
                nonlocal_rects.append((rx, cy, cw, ch))
                cy += ch
            return nonlocal_rects, (rx + cw, ry, rw - cw, rh)
        ch = s / rw if rw else 0
        cx = rx
        for a in row:
            cw = a / ch if ch else 0
            nonlocal_rects.append((cx, ry, cw, ch))
            cx += cw
        return nonlocal_rects, (rx, ry + ch, rw, rh - ch)

    i, row, rect = 0, [], (x, y, w, h)
    while i < len(areas):
        side = min(rect[2], rect[3])
        a = areas[i]
        if a <= 0:
            rects.append((rect[0], rect[1], 0.0, 0.0))
            i += 1
            continue
        if not row or worst(row + [a], side) <= worst(row, side):
            row.append(a)
            i += 1
        else:
            placed, rect = layout(row, *rect)
            rects.extend(placed)
            row = []
    if row:
        placed, _ = layout(row, *rect)
        rects.extend(placed)
    return rects


# --------------------------------------------------------------------------- журнал событий (вкладка «Ошибки»)
# Уровни Windows Event Log: 1 Critical, 2 Error, 3 Warning, 4 Information, 5 Verbose (0 — LogAlways, встречается как Information).
LEVELS = {1: "Критическая", 2: "Ошибка", 3: "Предупреждение", 4: "Информация", 5: "Подробно"}
LEVEL_KEYS = {1: "critical", 2: "error", 3: "warning", 4: "info", 5: "verbose"}
DEFAULT_LOGS = ("System", "Application")

_PS_EVENTS = r"""
$ErrorActionPreference = 'Stop'
$c = '__HOST__'
$logs = @(__LOGS__)
$levels = @(__LEVELS__)
$start = [datetime]::ParseExact('__START__', 'yyyy-MM-dd HH:mm', $null)
$end = [datetime]::ParseExact('__END__', 'yyyy-MM-dd HH:mm', $null)
$max = __MAX__
$out = @()
foreach ($l in $logs) {
    try {
        $f = @{ LogName = $l; StartTime = $start; EndTime = $end }
        if ($levels.Count -gt 0) { $f['Level'] = $levels }
        $ev = Get-WinEvent -ComputerName $c -FilterHashtable $f -MaxEvents $max -ErrorAction Stop
        $out += @($ev | ForEach-Object {
            $m = [string]$_.Message
            if ($m.Length -gt 600) { $m = $m.Substring(0, 600) + '…' }
            @{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); log = $l; level = [int]$_.Level; id = [int]$_.Id
               source = [string]$_.ProviderName; msg = $m }
        })
    } catch {
        if ($_.Exception.Message -notmatch 'No events were found|Не найдено событий') { $out += @(@{ error = "$l : $($_.Exception.Message)" }) }
    }
}
$out | ConvertTo-Json -Compress -Depth 3
"""


def parse_events_json(text: str) -> dict:
    """JSON → {events:[{time, log, level, level_text, id, source, msg}], errors:[…], summary:{critical, error, warning, info, by_source}}."""
    raw = json.loads(text) if text.strip() else []
    if isinstance(raw, dict):
        raw = [raw]
    events, errors = [], []
    for x in raw or []:
        if "error" in x:
            errors.append(str(x["error"]))
            continue
        lvl = int(x.get("level") or 4) or 4
        events.append({"time": x.get("time", ""), "log": x.get("log", ""), "level": lvl, "level_text": LEVELS.get(lvl, str(lvl)),
                       "id": int(x.get("id") or 0), "source": x.get("source", ""), "msg": (x.get("msg") or "").strip()})
    events.sort(key=lambda e: e["time"], reverse=True)
    return {"events": events, "errors": errors, "summary": summarize_events(events)}


def summarize_events(events: list[dict]) -> dict:
    """Сводка: сколько каждого уровня и топ источников среди критических и ошибок."""
    counts = {k: 0 for k in LEVEL_KEYS.values()}
    by_source: dict[str, int] = {}
    by_id: dict[tuple[str, int], dict] = {}
    for e in events:
        key = LEVEL_KEYS.get(e["level"], "info")
        counts[key] += 1
        if e["level"] in (1, 2):
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
            k = (e["source"], e["id"])
            if k not in by_id:
                by_id[k] = {"source": e["source"], "id": e["id"], "count": 0, "last": e["time"], "msg": e["msg"].split("\n")[0][:160]}
            by_id[k]["count"] += 1
    top_sources = sorted(by_source.items(), key=lambda kv: -kv[1])[:8]
    top_ids = sorted(by_id.values(), key=lambda d: (-d["count"], d["last"]))[:10]
    return {**counts, "total": len(events), "by_source": top_sources, "top": top_ids}


def get_events(host: str, start: datetime, end: datetime, levels: tuple[int, ...] = (1, 2, 3),
               logs: tuple[str, ...] = DEFAULT_LOGS, max_events: int = 500, timeout: int = 90) -> dict:
    """События System/Application за период с фильтром уровней. Только чтение (Get-WinEvent)."""
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    safe_logs = [l for l in logs if l.replace("-", "").replace("/", "").replace(" ", "").isalnum()]
    script = (_PS_EVENTS.replace("__HOST__", host)
              .replace("__LOGS__", ", ".join(f"'{l}'" for l in safe_logs))
              .replace("__LEVELS__", ", ".join(str(int(x)) for x in levels))
              .replace("__START__", start.strftime("%Y-%m-%d %H:%M")).replace("__END__", end.strftime("%Y-%m-%d %H:%M"))
              .replace("__MAX__", str(int(max_events))))
    out = _run_ps(script, timeout)
    if isinstance(out, dict):
        return out
    try:
        return parse_events_json(out)
    except (ValueError, KeyError) as exc:
        return {"error": f"Разбор ответа: {exc}"}


# --------------------------------------------------------------------------- запуск PowerShell
def _run_ps(script: str, timeout: int, cancelled=None, on_tick=None) -> dict | str:
    """Строка вывода PowerShell или dict с error (3.5.10: через :mod:`adk.psrun` — EncodedCommand, UTF-8,
    понятная причина отказа вместо «нет ответа»)."""
    from . import psrun
    res = psrun.run(script, timeout=timeout, cancelled=cancelled, on_tick=on_tick)
    if not res.ok:
        return {"error": res.error}
    return res.stdout


def get_health(host: str, timeout: int = 40) -> dict:
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    out = _run_ps(_PS.replace("__HOST__", host), timeout)
    if isinstance(out, dict):
        return out
    try:
        return parse_health_json(out)
    except (ValueError, KeyError) as exc:
        return {"error": f"Разбор ответа: {exc}"}


def get_disk_usage(host: str, drive: str = "C:", top: int = 40, timeout: int = 900, cancelled=None, on_tick=None) -> dict:
    """Карта диска через административный ресурс ``\\\\host\\C$``. Долго (минуты) — запускать только вручную."""
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    letter = (drive or "C").strip().rstrip(":\\$").upper()
    if len(letter) != 1 or not letter.isalpha():
        return {"error": f"Недопустимый диск: {drive!r}"}
    root = f"\\\\{host}\\{letter}$"
    out = _run_ps(_PS_USAGE.replace("__ROOT__", root).replace("__TOP__", str(int(top))), timeout, cancelled, on_tick)
    if isinstance(out, dict):
        return out
    try:
        return parse_usage_json(out)
    except (ValueError, KeyError) as exc:
        return {"error": f"Разбор ответа: {exc}"}
