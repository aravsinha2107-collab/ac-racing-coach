param([Parameter(Mandatory = $true)][string]$OutputPath)

# Output goes only to a local WAV file, never to a speaker or network service.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $enabledVoices = @($synthesizer.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($enabledVoices.Count -eq 0) {
        throw 'No enabled Windows desktop voice is installed.'
    }
    $synthesizer.Rate = 0
    $synthesizer.SetOutputToWaveFile($OutputPath)
    $synthesizer.Speak('First chicane ahead. Focus on a smooth brake release.')
    $synthesizer.SetOutputToNull()
    [Console]::WriteLine($synthesizer.Voice.Name)
} finally {
    $synthesizer.Dispose()
}
