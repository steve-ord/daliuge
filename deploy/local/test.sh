#!/bin/bash
# setup python path to src
DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
pushd ./ >> /dev/null
cd $DIR
cd ../../
export PYTHONPATH=$(pwd)/src/:$PYTHONPATH
popd >> /dev/null

# execute unit tests
python ../../test/unit/test_data_object.py
exit $?