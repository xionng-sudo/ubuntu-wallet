package collector

import (
	"os"
	"strings"
)

func allowMock() bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv("ALLOW_MOCK")))
	// default true
	if v == "" {
		return true
	}
	return v == "1" || v == "true" || v == "yes" || v == "y"
}

func prefix(s string, n int) string {
	if n <= 0 {
		return ""
	}
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func truncateForLog(b []byte, max int) string {
	if max <= 0 || len(b) == 0 {
		return ""
	}
	if len(b) <= max {
		return string(b)
	}
	return string(b[:max]) + "...(truncated)"
}
