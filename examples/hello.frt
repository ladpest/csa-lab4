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

"Hello, world!\n" print-cstr
