: print-cstr
    dup @
    dup if
        emit
        1 +
        print-cstr
    else
        drop
        drop
    then
;

: read-line-cstr
    key
    dup 10 = if
        drop
        0 swap !
    else
        over !
        1 +
        read-line-cstr
    then
;

"What is your name?\n" print-cstr
900 read-line-cstr
"Hello, " print-cstr
900 print-cstr
33 emit
10 emit
