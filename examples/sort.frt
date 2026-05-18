\ Sort three input digit characters in ascending order.
key 820 !
key 821 !
key 822 !

820 @ 821 @ > if
    820 @ 823 !
    821 @ 820 !
    823 @ 821 !
then

821 @ 822 @ > if
    821 @ 823 !
    822 @ 821 !
    823 @ 822 !
then

820 @ 821 @ > if
    820 @ 823 !
    821 @ 820 !
    823 @ 821 !
then

820 @ emit
821 @ emit
822 @ emit
10 emit
