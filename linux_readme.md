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


# service
[Unit]
Description=RSID CLI Service
After=network-online.target
Wants=network-online.target

[Service]
User=gatevision
Group=gatevision
WorkingDirectory=/home/gatevision/Desktop/RSID_Face_Guard

ExecStartPre=/bin/sleep 10

ExecStart=/home/gatevision/Desktop/RSID_Face_Guard/.venv/bin/python /home/gatevision/Desktop/RSID_Face_Guard/auth_cli.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target