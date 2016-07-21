#!/bin/bash

for mod in netforce*; do
    echo "installing $mod..."
    cd $mod
    ./setup.py develop
    cd ..
done
