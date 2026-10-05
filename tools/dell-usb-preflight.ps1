# Read-only. This script NEVER partitions, erases, writes or changes EFI boot settings.
[CmdletBinding()]
param([int]$DiskNumber=-1,[string]$ExpectedPnpId="")
$ErrorActionPreference="Stop"
$rows=@()
foreach($disk in @(Get-Disk | Where-Object BusType -eq USB)){
  $wmi=Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq $disk.Number } | Select-Object -First 1
  $parts=@(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)
  $vols=@(foreach($p in $parts){
    $v=$p | Get-Volume -ErrorAction SilentlyContinue
    if($v){[PSCustomObject]@{Letter=$v.DriveLetter;Label=$v.FileSystemLabel;Format=$v.FileSystem;SizeBytes=$p.Size}}
  })
  $protected=@($vols | Where-Object {$_.Label -match 'DOKUMENTE|BACKUP|SYSTEM|RECOVERY'}).Count -gt 0
  $rows+= [PSCustomObject]@{
    DiskNumber=$disk.Number;Model=$disk.FriendlyName;PnpId=$wmi.PNPDeviceID
    SizeBytes=$disk.Size;IsBoot=$disk.IsBoot;IsSystem=$disk.IsSystem
    Volumes=$vols;ProtectedLabel=$protected
    EligibleForReview=($disk.BusType -eq 'USB' -and !$disk.IsBoot -and !$disk.IsSystem -and $disk.Size -ge 8GB -and !$protected)
  }
}
if($DiskNumber -lt 0){
  [PSCustomObject]@{Mode='READ_ONLY_INVENTORY';Disks=$rows} | ConvertTo-Json -Depth 6
  exit 0
}
$target=@($rows | Where-Object {$_.DiskNumber -eq $DiskNumber})
if($target.Count -ne 1){throw 'No unique USB disk with that number'}
$t=$target[0]
if(!$t.EligibleForReview){throw 'Disk is a system disk, too small, or has a protected label'}
if(!$ExpectedPnpId -or $t.PnpId -cne $ExpectedPnpId){throw 'Full PNP identifier is required and must match exactly'}
[PSCustomObject]@{
  Mode='READ_ONLY_TARGET_CHECK';Result='ELIGIBLE_FOR_MANUAL_REVIEW'
  DiskNumber=$t.DiskNumber;Model=$t.Model;PnpId=$t.PnpId
  SizeBytes=$t.SizeBytes;ExistingVolumes=$t.Volumes
  NextStep='STOP. This script does not flash. Confirm all existing files are disposable before any future write.'
} | ConvertTo-Json -Depth 6
