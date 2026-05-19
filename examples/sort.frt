\ Sort an input line of digit characters in ascending order.
\ The line is stored as a C-style character array at address 830.
0 820 !
begin
    key 821 !
    821 @ 10 = if
        1
    else
        821 @ 830 820 @ + !
        820 @ 1 + 820 !
        0
    then
until

820 @ 1 > if
    0 822 !
    begin
        0 823 !
        begin
            830 823 @ + @ 824 !
            830 823 @ 1 + + @ 825 !
            824 @ 825 @ > if
                825 @ 830 823 @ + !
                824 @ 830 823 @ 1 + + !
            then
            823 @ 1 + 823 !
            823 @ 820 @ 1 - 822 @ - =
        until
        822 @ 1 + 822 !
        822 @ 820 @ 1 - =
    until
then

0 823 !
begin
    830 823 @ + @ emit
    823 @ 1 + 823 !
    823 @ 820 @ =
until
10 emit
