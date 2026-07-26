<#
Creates a reviewed commit from tracked project files and pushes it to the
configured private GitHub remote. It never adds ignored runtime data.

Usage:
  .\sync_github.ps1
  .\sync_github.ps1 -Message "Describe this update"
#>
[CmdletBinding()]
param(
    [string]$Message
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $RepoRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not installed or is not available on PATH.'
}

$remotes = git remote
if ($remotes -notcontains 'origin') {
    throw 'GitHub remote "origin" is not configured. Complete GitHub setup first.'
}

git add --all
$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host 'No tracked project changes to sync.'
    exit 0
}

Write-Host 'Files to sync:'
$staged | ForEach-Object { Write-Host "  $_" }

if (-not $Message) {
    $Message = "Update $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}

git commit -m $Message
git push -u origin HEAD
Write-Host 'GitHub sync complete.'
