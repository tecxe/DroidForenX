from desktop.core import adb_manager as adb
serial = '144131557N016694'

# Test: escaped parens
ok, out = adb.adb_shell(serial, r'find /sdcard/ -type f \( -name "*.pdf" -o -name "*.docx" \) 2>/dev/null | head -5', timeout=10)
print(f"Escaped parens: ok={ok}, out={repr(out[:200]) if out else 'empty'}")

# Test: no parens
ok2, out2 = adb.adb_shell(serial, r'find /sdcard/ -type f -name "*.pdf" 2>/dev/null | head -5', timeout=10)
print(f"No parens: ok={ok2}, out={repr(out2[:200]) if out2 else 'empty'}")

# Test: deleted combined
ok3, out3 = adb.adb_shell(serial, r'find /sdcard/ -name ".trashed*" -type f 2>/dev/null | head -5', timeout=10)
print(f"Deleted: ok={ok3}, out={repr(out3[:200]) if out3 else 'empty'}")

# Test: chrome
ok4, out4 = adb.adb_shell(serial, r'find /sdcard/Android/data/com.android.chrome/ -type f -name "Bookmarks" 2>/dev/null | head -5', timeout=10)
print(f"Chrome: ok={ok4}, out={repr(out4[:200]) if out4 else 'empty'}")

# Test: combined deleted (the broken one)
ok5, out5 = adb.adb_shell(serial, r'find /sdcard/ \( -name ".trashed*" -o -name ".deleted*" -o -name "*.trashed" -o -path "*/.trash/*" -o -path "*/.Trash/*" -o -path "*/Lost.dir/*" -o -name "*.db-wal" -o -name "*.db-shm" -o -name "*.wal" -o -name "*.shm" -o -path "*/.thumbnails/*" -o -name ".recycle*" \) -type f 2>/dev/null | head -5', timeout=15)
print(f"Combined deleted: ok={ok5}, out={repr(out5[:200]) if out5 else 'empty'}")
