
syntax match ContextDate "\v\d{4}-\d{2}-\d{2}" 

syntax match Time "\dh"
syntax match Time "\dh\dm"
syntax match Time "\dh\d\dm"
syntax match Time "\dm"
syntax match Time "\d\dm"
syntax match Time "\v\.+"

syntax match Character "\$ "
syntax match Character "x "

syntax match Type "\v\+\w+"
syntax match Label "\v\@\w+"

syntax region Comment start="//" end="$"

syntax region Added start=">" end="$"
syntax region Removed start="<" end="$"

highlight link ContextDate Keyword
highlight link Time Number
highlight link Project Identifier


