." What is your name?\n"
." Hello, "
begin
    key
    dup 10 = if
        drop
        33 emit
        10 emit
        1
    else
        emit
        0
    then
until
