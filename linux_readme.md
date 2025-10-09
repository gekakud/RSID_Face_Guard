# install cmake
sudo apt install cmake gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib

# build realsenseid
mkdir build
cd build
cmake .. -DRSID_PY=ON -DRSID_PREVIEW=ON -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release

# run ide

sudo code . --no-sandbox --user-data-dir ./datadir
source ./.venv/bin/activate

# demo service run - no GUI
/home/gatevision/Desktop/RSID_Face_Guard/.venv/bin/python /home/gatevision/Desktop/RSID_Face_Guard/auth_cli.py
