\ Project Euler problem 4: largest palindromic product of two 3-digit numbers.
\ One factor of any 6-digit palindrome is divisible by 11, so j steps by 11.
0 800 !
999 801 !
begin
    990 802 !
    begin
        801 @ 802 @ * 803 !

        803 @ 800 @ > if
            803 @ 804 !
            0 805 !
            begin
                805 @ 10 * 804 @ 10 mod + 805 !
                804 @ 10 / 804 !
                804 @ 0 =
            until

            803 @ 805 @ = if
                803 @ 800 !
            then
        then

        802 @ 11 - dup 802 !
        899 <
    until

    801 @ 1 - dup 801 !
    899 =
until
800 @ .
