// 自定义 checker：argv = <input> <output> <answer>；退出码 0=AC，1=WA，2=SE。
#include <bits/stdc++.h>
int main(int argc, char* argv[]) {
    if (argc < 4) {
        std::cerr << "usage: checker <input> <output> <answer>\n";
        return 2;
    }
    std::ifstream ans(argv[3]), ouf(argv[2]);
    long long ja, pa;
    if (!(ans >> ja)) { std::cerr << "bad answer\n"; return 2; }
    if (!(ouf >> pa)) { std::cerr << "no output\n"; return 1; }
    std::string extra;
    if (ouf >> extra) { std::cerr << "extra output\n"; return 1; }
    if (ja != pa) {
        std::cerr << "expected " << ja << ", found " << pa << "\n";
        return 1;
    }
    return 0;
}
