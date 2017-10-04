#!/bin/bash -l

if [ $# -gt 0 ]; then
    TOPDIR=$1
else
    TOPDIR=daliuge
fi
#
# first we need to build daliuge
# via a virtualenv
# 
# test are we on galaxy
if [[ $(hostname -s) = galaxy-? ]]; then
    module load virtualenv
fi

mkdir ${WORKSPACE}/daliuge_env
virtualenv daliuge_env
cd ${WORKSPACE}/daligue_env/bin
if [ $? -ne 0 ]; then
    echo "Error: Failed to chdir to  ${WORKSPACE}/daliuge_env/bin"
    exit 1
fi

source activate

cd $WORKSPACE/${TOPDIR}
if [ $? -ne 0 ]; then
    echo "Error: Failed to chdir to  ${WORKSPACE}/${TOPDIR}"
    exit 1
fi

#
#
#
#
#
python setup.py install
if [ $? -ne 0 ]; then
    echo "Error: installation failed"
    exit 1
fi

cd $WORKSPACE/daliuge_env/bin
deactivate

