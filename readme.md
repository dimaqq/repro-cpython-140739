## Python 3.15a1 / a2 crash

This is the reproducer for https://github.com/python/cpython/issues/140739

Check out this repository and run:

```sh
# 3.15a1, 3.15a2
sudo -H uvx -p 3.15t --with "ops[testing]==3.3.1" python -m profiling.sampling test.py 100 100
# or
sudo -H uvx -p 3.15t --with "ops[testing]==3.3.1" python -m profiling.sampling --mode=gil test.py 100 100

# 3.15a3
sudo -H uvx -p 3.15t --with "ops[testing]==3.3.1" python -m profiling.sampling run test.py 100 100
```

Note that the latest "ops" cannot be used because of an internal change where some data can no longer be pickled.


### Tested on macOS
hardware: m2 aarch64
cpython-3.15.0a2+freethreaded (astral standalone)
cpython-3.15.0a1+freethreaded (astral standalone)
cpython-3.15.0a3 (downloaded from python.org)

Two symptoms are observed, possibly the same crash:

```
Resetting... [....................................................................................................]
🦐/c/repro-cpython-140739 (main) [SIGBUS]>
```

and

```
Resetting... [....................................................................................................]
Reset [0...................................................................................................]
Reset [0001110001010101111110001110010101010101001010101000111101010111000111000101010111111000111001010101]
0: [0001110001010101111110001110010101010101001010101000111101010111000111000101010111111000111001010101] --> 0
0: [0...................................................................................................]
0: [0011010101001100010111010000010111000101010000000100000100010100010101100000110011000101011001000001] --> 1
🦐/c/repro-cpython-140739 (main) [SIGSEGV]>
```

I hazard a guess that SIGBUS and SIGSEGV are probably the same issue.


### Tested on Linux
hardware: AMD amd64
cpython-3.15.0a2+freethreaded-linux-x86_64-gnu

```
Resetting... [....................................................................................................]
Reset [0...................................................................................................]
Reset [0001110001010101111110001110010101010101001010101000111101010111000111000101010111111000111001010101]
0: [0001110001010101111110001110010101010101001010101000111101010111000111000101010111111000111001010101] --> 0
0: [0...................................................................................................]
0: [0011010101001100010111010000010111000101010000000100000100010100010101100000110011000101011001000001] --> 1
1: [0...................................................................................................]
⋊> dima@bb ⋊> /c/repro-cpython-140739 on main  echo $status
139
```

Exit code 139 means that the process died with SIGSEGV
