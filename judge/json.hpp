// cflite::Json —— 极简 JSON 解析/序列化（头文件版，无第三方依赖）。
// 支持 object/array/string/number/bool/null，字符串内保留原始 UTF-8 字节。
#pragma once
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace cflite {

class Json {
public:
    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json>;
    enum class Type { Null, Bool, Int, Double, String, Array, Object };

private:
    std::variant<std::nullptr_t, bool, long long, double, std::string, Array, Object> _v;
    Type _t = Type::Null;

public:
    Json() : _v(nullptr), _t(Type::Null) {}
    Json(std::nullptr_t) : _v(nullptr), _t(Type::Null) {}
    Json(bool b) : _v(b), _t(Type::Bool) {}
    Json(int v) : _v((long long)v), _t(Type::Int) {}
    Json(unsigned v) : _v((long long)v), _t(Type::Int) {}
    Json(long long v) : _v(v), _t(Type::Int) {}
    Json(double v) : _v(v), _t(Type::Double) {}
    Json(const char* s) : _v(std::string(s ? s : "")), _t(Type::String) {}
    Json(std::string s) : _v(std::move(s)), _t(Type::String) {}
    Json(const Array& a) : _v(a), _t(Type::Array) {}
    Json(const Object& o) : _v(o), _t(Type::Object) {}

    Type type() const { return _t; }
    bool is_null() const { return _t == Type::Null; }
    bool is_bool() const { return _t == Type::Bool; }
    bool is_number() const { return _t == Type::Int || _t == Type::Double; }
    bool is_string() const { return _t == Type::String; }
    bool is_array() const { return _t == Type::Array; }
    bool is_object() const { return _t == Type::Object; }

    bool as_bool() const { return std::get<bool>(_v); }
    long long as_int() const {
        if (_t == Type::Int) return std::get<long long>(_v);
        if (_t == Type::Double) return (long long)std::get<double>(_v);
        throw std::runtime_error("Json: not a number");
    }
    double as_double() const {
        if (_t == Type::Double) return std::get<double>(_v);
        if (_t == Type::Int) return (double)std::get<long long>(_v);
        throw std::runtime_error("Json: not a number");
    }
    const std::string& as_string() const {
        if (_t != Type::String) throw std::runtime_error("Json: not a string");
        return std::get<std::string>(_v);
    }
    const Array& as_array() const {
        if (_t != Type::Array) throw std::runtime_error("Json: not an array");
        return std::get<Array>(_v);
    }
    const Object& as_object() const {
        if (_t != Type::Object) throw std::runtime_error("Json: not an object");
        return std::get<Object>(_v);
    }
    Array& as_array() {
        if (_t != Type::Array) { _v = Array{}; _t = Type::Array; }
        return std::get<Array>(_v);
    }
    Object& as_object() {
        if (_t != Type::Object) { _v = Object{}; _t = Type::Object; }
        return std::get<Object>(_v);
    }

    size_t size() const {
        if (_t == Type::Array) return std::get<Array>(_v).size();
        if (_t == Type::Object) return std::get<Object>(_v).size();
        return 0;
    }
    bool empty() const { return size() == 0; }

    bool contains(const std::string& key) const {
        if (_t != Type::Object) return false;
        return std::get<Object>(_v).count(key) > 0;
    }

    const Json& operator[](const std::string& key) const {
        const auto& o = std::get<Object>(_v);
        auto it = o.find(key);
        if (it == o.end()) throw std::runtime_error("Json: missing key '" + key + "'");
        return it->second;
    }
    const Json& operator[](size_t i) const { return std::get<Array>(_v).at(i); }

    Json get(const std::string& key, const Json& dflt) const {
        if (_t != Type::Object) return dflt;
        const auto& o = std::get<Object>(_v);
        auto it = o.find(key);
        return it == o.end() ? dflt : it->second;
    }

    void set(const std::string& key, Json v) {
        if (_t != Type::Object) { _v = Object{}; _t = Type::Object; }
        std::get<Object>(_v)[key] = std::move(v);
    }
    void push_back(Json v) {
        if (_t != Type::Array) { _v = Array{}; _t = Type::Array; }
        std::get<Array>(_v).push_back(std::move(v));
    }

    std::string dump() const;
    static Json parse(const std::string& s);
};

// ---------------- serialization ----------------
inline std::string _json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    return out;
}

inline std::string Json::dump() const {
    switch (_t) {
        case Type::Null: return "null";
        case Type::Bool: return std::get<bool>(_v) ? "true" : "false";
        case Type::Int: return std::to_string(std::get<long long>(_v));
        case Type::Double: {
            char buf[40];
            std::snprintf(buf, sizeof(buf), "%.17g", std::get<double>(_v));
            return buf;
        }
        case Type::String: return "\"" + _json_escape(std::get<std::string>(_v)) + "\"";
        case Type::Array: {
            std::string out = "[";
            const auto& a = std::get<Array>(_v);
            for (size_t i = 0; i < a.size(); ++i) {
                if (i) out += ",";
                out += a[i].dump();
            }
            out += "]";
            return out;
        }
        case Type::Object: {
            std::string out = "{";
            const auto& o = std::get<Object>(_v);
            size_t i = 0;
            for (const auto& [k, v] : o) {
                if (i++) out += ",";
                out += "\"" + _json_escape(k) + "\":" + v.dump();
            }
            out += "}";
            return out;
        }
    }
    return "null";
}

// ---------------- parsing ----------------
namespace {
struct JsonParser {
    const std::string& s;
    size_t i = 0;

    void skip_ws() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r'))
            ++i;
    }
    [[noreturn]] void fail(const std::string& msg) {
        throw std::runtime_error("JSON parse error at " + std::to_string(i) + ": " + msg);
    }
    void expect(char c) {
        skip_ws();
        if (i >= s.size() || s[i] != c) fail(std::string("expected '") + c + "'");
        ++i;
    }

    Json parse_value() {
        skip_ws();
        if (i >= s.size()) fail("unexpected end");
        char c = s[i];
        if (c == '{') return parse_object();
        if (c == '[') return parse_array();
        if (c == '"') return Json(parse_string());
        if (c == 't') { expect_word("true"); return Json(true); }
        if (c == 'f') { expect_word("false"); return Json(false); }
        if (c == 'n') { expect_word("null"); return Json(nullptr); }
        return parse_number();
    }
    void expect_word(const char* w) {
        size_t n = std::strlen(w);
        if (s.compare(i, n, w) != 0) fail("invalid literal");
        i += n;
    }
    Json parse_object() {
        expect('{');
        Json::Object o;
        skip_ws();
        if (i < s.size() && s[i] == '}') { ++i; return Json(o); }
        while (true) {
            skip_ws();
            if (i >= s.size() || s[i] != '"') fail("expected string key");
            std::string key = parse_string();
            expect(':');
            o[key] = parse_value();
            skip_ws();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == '}') { ++i; return Json(o); }
            fail("expected ',' or '}'");
        }
    }
    Json parse_array() {
        expect('[');
        Json::Array a;
        skip_ws();
        if (i < s.size() && s[i] == ']') { ++i; return Json(a); }
        while (true) {
            a.push_back(parse_value());
            skip_ws();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == ']') { ++i; return Json(a); }
            fail("expected ',' or ']'");
        }
    }
    static int hexval(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }
    void append_utf8(std::string& out, unsigned cp) {
        if (cp <= 0x7F) out += (char)cp;
        else if (cp <= 0x7FF) { out += (char)(0xC0 | (cp >> 6)); out += (char)(0x80 | (cp & 0x3F)); }
        else if (cp <= 0xFFFF) {
            out += (char)(0xE0 | (cp >> 12)); out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18)); out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F)); out += (char)(0x80 | (cp & 0x3F));
        }
    }
    std::string parse_string() {
        expect('"');
        std::string out;
        while (true) {
            if (i >= s.size()) fail("unterminated string");
            char c = s[i++];
            if (c == '"') return out;
            if (c == '\\') {
                if (i >= s.size()) fail("bad escape");
                char e = s[i++];
                switch (e) {
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case 'u': {
                        if (i + 4 > s.size()) fail("bad \\u");
                        int cp = 0;
                        for (int k = 0; k < 4; ++k) {
                            int h = hexval(s[i++]);
                            if (h < 0) fail("bad \\u hex");
                            cp = cp * 16 + h;
                        }
                        // 处理代理对
                        if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= s.size() &&
                            s[i] == '\\' && s[i + 1] == 'u') {
                            int lo = 0;
                            bool ok = true;
                            for (int k = 2; k < 6; ++k) {
                                int h = hexval(s[i + k]);
                                if (h < 0) { ok = false; break; }
                                lo = lo * 16 + h;
                            }
                            if (ok && lo >= 0xDC00 && lo <= 0xDFFF) {
                                i += 6;
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            }
                        }
                        append_utf8(out, (unsigned)cp);
                        break;
                    }
                    default: fail("bad escape char");
                }
            } else {
                out += c;
            }
        }
    }
    Json parse_number() {
        size_t start = i;
        if (i < s.size() && (s[i] == '-' || s[i] == '+')) ++i;
        while (i < s.size() && (std::isdigit((unsigned char)s[i]) || s[i] == '.' ||
               s[i] == 'e' || s[i] == 'E' || s[i] == '-' || s[i] == '+'))
            ++i;
        std::string tok = s.substr(start, i - start);
        if (tok.empty()) fail("bad number");
        bool is_float = tok.find_first_of(".eE") != std::string::npos;
        if (is_float) {
            return Json(std::strtod(tok.c_str(), nullptr));
        }
        return Json((long long)std::strtoll(tok.c_str(), nullptr, 10));
    }
};
}  // namespace

inline Json Json::parse(const std::string& s) {
    JsonParser p{s};
    Json v = p.parse_value();
    p.skip_ws();
    if (p.i != s.size()) throw std::runtime_error("JSON: trailing characters");
    return v;
}

}  // namespace cflite
