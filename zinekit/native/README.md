# native

The Punk Zine frei0r plugin that zinekit runs.

- `punkzine.c` is a copy of `src/filter/punkzine/punkzine.c` from the frei0r fork (<https://github.com/cookieukw/frei0r>, branch `filter/punkzine`), where the filter is developed. MIT.
- `frei0r.h` is a minimal declaration of the frei0r 1.2 plugin API (only what the plugin uses). Building inside the frei0r tree uses the official `include/frei0r.h` instead; both give the same ABI.

`zinekit/engine.py` compiles these with

```sh
cc -O3 -fPIC -shared -I . -o punkzine-<hash>.so punkzine.c -lm -lpthread
```

into `~/.cache/zinekit/` the first time it is needed, and again whenever either file changes (the hash covers both files and the flags). `zinekit build --force` rebuilds by hand.

To update: copy the new `punkzine.c` here, then run `python3 -m unittest tests.test_engine`. The tests compare the parameter names, order and types of the compiled plugin with `zinekit/params.py`.
