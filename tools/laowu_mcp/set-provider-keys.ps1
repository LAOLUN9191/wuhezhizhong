param(
    [string]$UserConfigPath,
    [switch]$ForRunner,
    [string]$SetKey,
    [string]$SetKeyName,
    [string]$ClearProviderKey,
    [string]$ClearKeyName,
    [switch]$ClearAll,
    [switch]$SetKeyFromStdin
)
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$ErrorActionPreference = 'Stop'
$securityModule = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
Import-Module $securityModule -ErrorAction Stop
$configPath = if ($UserConfigPath) { [IO.Path]::GetFullPath($UserConfigPath) } else { Join-Path $PSScriptRoot 'user_config.json' }
if (-not (Test-Path -LiteralPath $configPath)) { throw 'Local user configuration is missing.' }
$config = Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
$storeName = $config.paths.credential_store
if (-not $storeName) { $storeName = 'keys.xml' }
$storePath = if ([IO.Path]::IsPathRooted($storeName)) { $storeName } else { Join-Path (Split-Path -Parent $configPath) $storeName }
$providers = @($config.providers.PSObject.Properties | ForEach-Object { $_.Value })
$names = @($providers | ForEach-Object { $_.key_name } | Where-Object { $_ } | Select-Object -Unique)

function Convert-SecureStringToPlainText([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Set-PrivateFileAcl([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetOwner($identity.User)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity.User, (New-Object -TypeName Security.Principal.SecurityIdentifier -ArgumentList 'S-1-5-18'), (New-Object -TypeName Security.Principal.SecurityIdentifier -ArgumentList 'S-1-5-32-544'))) {
        $rule = New-Object -TypeName Security.AccessControl.FileSystemAccessRule -ArgumentList @($sid, [Security.AccessControl.FileSystemRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Save-PrivateClixml($Value) {
    $directory = Split-Path -Parent $storePath
    if (-not $directory) { $directory = (Get-Location).Path }
    if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $temporaryPath = Join-Path $directory ('.' + [IO.Path]::GetRandomFileName())
    $backupPath = Join-Path $directory ('.' + [IO.Path]::GetRandomFileName())
    try {
        $stream = [IO.File]::Open($temporaryPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $stream.Dispose()
        Set-PrivateFileAcl $temporaryPath
        $Value | Export-Clixml -LiteralPath $temporaryPath -Force
        if (Test-Path -LiteralPath $storePath) {
            [IO.File]::Replace($temporaryPath, $storePath, $backupPath)
        } else {
            [IO.File]::Move($temporaryPath, $storePath)
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force }
        if (Test-Path -LiteralPath $backupPath) { Remove-Item -LiteralPath $backupPath -Force }
    }
}

$storeMutex = [System.Threading.Mutex]::new($false, 'Local\WuheSuZhongCredentialStore-v1')
$storeMutexOwned = $false
try {
    try { $storeMutexOwned = $storeMutex.WaitOne([TimeSpan]::FromSeconds(30)) }
    catch [System.Threading.AbandonedMutexException] { $storeMutexOwned = $true }
    if (-not $storeMutexOwned) { throw 'Credential store is busy.' }

    try {
$stored = if (Test-Path -LiteralPath $storePath) { Import-Clixml -LiteralPath $storePath } else { @{} }
if ($ClearProviderKey) {
    $provider = $config.providers.($ClearProviderKey)
    if (-not $provider -or -not $provider.key_name) { throw "Unknown configured provider '$ClearProviderKey'." }
    if ((Test-Path -LiteralPath $storePath) -and $stored.Remove([string]$provider.key_name)) {
        Save-PrivateClixml $stored
    }
    Write-Host "Saved key for provider '$ClearProviderKey' is cleared."
    exit 0
}
if ($ClearKeyName) {
    if ($names -notcontains $ClearKeyName) { throw 'Unknown configured credential name.' }
    if ((Test-Path -LiteralPath $storePath) -and $stored.Remove($ClearKeyName)) { Save-PrivateClixml $stored }
    Write-Host 'Saved key is cleared.'
    exit 0
}
if ($ForRunner) {
    $plain = [ordered]@{}
    foreach ($name in $names) {
        $value = $stored[$name]
        if ($null -ne $value) { $plain[$name] = Convert-SecureStringToPlainText $value }
    }
    ConvertTo-Json -InputObject $plain -Compress
    exit 0
}
if ($ClearAll) {
    if (Test-Path -LiteralPath $storePath) { Remove-Item -LiteralPath $storePath -Force }
    Write-Host 'Saved configuration is cleared for this user.'
    exit 0
}
if ($SetKeyFromStdin) {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $provider = $config.providers.($payload.provider_id)
    $keyName = if ($payload.key_name) { [string]$payload.key_name } else { [string]$provider.key_name }
    $plainKey = [string]$payload.api_key
    if (-not $keyName -or $names -notcontains $keyName -or [string]::IsNullOrWhiteSpace($plainKey) -or $plainKey.Length -gt 8192) {
        throw 'Invalid provider or key.'
    }
    $stored[$keyName] = ConvertTo-SecureString -String $plainKey -AsPlainText -Force
    Save-PrivateClixml $stored
    $plainKey = $null
    $payload = $null
    [Console]::Out.WriteLine('Saved encrypted key for this Windows user.')
    exit 0
}
if ($SetKeyName) {
    if ($names -notcontains $SetKeyName) { throw 'Unknown configured credential name.' }
    $value = Read-Host "Enter key for $SetKeyName" -AsSecureString
    if ($value.Length -eq 0) { throw 'A key is required.' }
    $stored[$SetKeyName] = $value
    Save-PrivateClixml $stored
    Write-Host 'Saved encrypted key for this Windows user.'
    exit 0
}
if ($SetKey) {
    $provider = $config.providers.$SetKey
    if (-not $provider -or -not $provider.key_name) { throw 'Unknown configured provider.' }
    $value = Read-Host "Enter key for $($provider.label)" -AsSecureString
    if ($value.Length -eq 0) { throw 'A key is required.' }
    $stored[$provider.key_name] = $value
    Save-PrivateClixml $stored
    Write-Host 'Saved encrypted key for this Windows user.'
    exit 0
}
foreach ($providerEntry in $config.providers.PSObject.Properties) {
    $provider = $providerEntry.Value
    if (-not $provider.key_name) { continue }
    $value = Read-Host "Enter key for $($provider.label)" -AsSecureString
    if ($value.Length -gt 0) { $stored[$provider.key_name] = $value }
}
Save-PrivateClixml $stored
Write-Host 'Saved encrypted keys for this Windows user.'
    } finally {
        if ($storeMutexOwned) { $storeMutex.ReleaseMutex() }
    }
} finally {
    $storeMutex.Dispose()
}
