on run
    set resourceDirectory to (POSIX path of (path to me)) & "Contents/Resources/"
    do shell script "/bin/sh " & quoted form of (resourceDirectory & "launch.sh") & " >/dev/null 2>&1 &"
end run
