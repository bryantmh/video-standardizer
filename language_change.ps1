param(
    [string]$folderPath
)

# Check if the folder exists
if (-not (Test-Path $folderPath -PathType Container)) {
    Write-Error "Folder does not exist: $folderPath"
    exit 1
}

# Get all files in the folder
$files = Get-ChildItem -Path $folderPath -File

foreach ($file in $files) {
    $inputFile = $file.FullName
    
    # Use ffprobe to get the number of audio streams
    $streamOutput = & ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 $inputFile
    $streamCount = ($streamOutput -split "`n" | Where-Object { $_ -ne "" }).Count
    
    # Build FFmpeg arguments
    $args = @("-i", $inputFile, "-map", "0", "-c", "copy")
    for ($i = 0; $i -lt $streamCount; $i++) {
        $args += "-metadata:s:a:$i", "language=eng"
    }
    
    # Construct the output file name
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($inputFile)
    $ext = [System.IO.Path]::GetExtension($inputFile)
    $outputFile = Join-Path -Path $folderPath -ChildPath "$baseName`_eng$ext"
    
    # Add output file to arguments
    $args += $outputFile
    
    # Execute FFmpeg
    & ffmpeg $args
}