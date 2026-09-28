#!/bin/sh
# mkmetallib.sh SRC OUT: compile SRC with the offline Metal compiler and write OUT, a C header
# holding the .metallib bytes.  Tries the selected developer dir, then Xcode.app.  If no Metal
# compiler is usable, OUT is written empty and l11gpu.m compiles SRC at run time instead.
src=$1; out=$2; tmp=${out}.tmp$$
for dev in "${DEVELOPER_DIR:-}" /Applications/Xcode.app/Contents/Developer; do
	if [ -n "$dev" ]; then env="DEVELOPER_DIR=$dev"; else env=""; fi
	if env $env xcrun -sdk macosx metal -mmacosx-version-min=12.0 -c "$src" -o "$tmp.air" 2>/dev/null &&
	   env $env xcrun -sdk macosx metallib "$tmp.air" -o "$tmp.metallib" 2>/dev/null; then
		{
			echo "#define L11GPU_HAVE_METALLIB 1"
			echo "static const unsigned char l11gpu_metallib[] = {"
			xxd -i < "$tmp.metallib"
			echo "};"
		} > "$out"
		rm -f "$tmp.air" "$tmp.metallib"
		echo "mkmetallib: compiled $src offline${dev:+ with $dev}" 1>&2
		exit 0
	fi
done
rm -f "$tmp.air" "$tmp.metallib"
echo "/* no offline Metal compiler: the shader is compiled at run time */" > "$out"
echo "mkmetallib: no Metal compiler found; the shader will be compiled at run time" 1>&2
exit 0
