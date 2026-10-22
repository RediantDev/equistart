#!/bin/bash
# Полный прогон на чистой базе: 4 турнира + сложные случаи
cd /home/claude/sim
./reset.sh
python3 -u scenario2.py > s2.log 2>&1; echo "S2 done $(tail -1 s2.log)"
python3 -u scenario4.py > s4.log 2>&1; echo "S4 done $(tail -1 s4.log)"
python3 -u scenario1.py > s1.log 2>&1 &
P1=$!
sleep 30
python3 -u scenario3.py > s3.log 2>&1 &
P3=$!
wait $P1; echo "S1 done $(tail -1 s1.log)"
wait $P3; echo "S3 done $(tail -1 s3.log)"
python3 -u scenario5.py > s5.log 2>&1; echo "S5 done $(tail -1 s5.log)"
python3 -u scenario6.py > s6.log 2>&1; echo "S6 done $(grep CHECKS s6.log)"
echo ALL-DONE
