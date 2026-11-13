// Hack 输入校验器：两个整数 a b，范围 [-1e9, 1e9]。退出码 0=合法，非 0=非法。
#include <bits/stdc++.h>
int main() {
    long long a, b;
    if (!(std::cin >> a >> b)) return 1;
    std::string extra;
    if (std::cin >> extra) return 1;
    if (a < -1000000000LL || a > 1000000000LL) return 1;
    if (b < -1000000000LL || b > 1000000000LL) return 1;
    return 0;
}
