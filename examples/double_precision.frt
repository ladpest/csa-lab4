\ Demonstrate 64-bit arithmetic on 32-bit machine words.
\ The number is stored as two words: high at 901, low at 900.
\ Start with 0x00000000FFFFFFFF and add 1 with carry.
-1 900 !
0 901 !
900 @ 1 + 900 !
900 @ 0 = if
    901 @ 1 + 901 !
then
901 @ .
900 @ .
