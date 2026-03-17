package signal

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
)

func ensureDir(dir string) error {
	return os.MkdirAll(dir, 0755)
}

// WriteLatest keeps backward compatibility (signals_1h_latest.json).
func WriteLatest(dataDir string, res *SignalResult) (string, error) {
	return WriteLatestNamed(dataDir, "signals_1h_latest.json", res)
}

// WriteLatestRules writes rules latest to signals_1h_latest_rules.json.
func WriteLatestRules(dataDir string, res *SignalResult) (string, error) {
	return WriteLatestNamed(dataDir, "signals_1h_latest_rules.json", res)
}

// WriteLatestML writes ml latest to signals_1h_latest_ml.json.
func WriteLatestML(dataDir string, res *SignalResult) (string, error) {
	return WriteLatestNamed(dataDir, "signals_1h_latest_ml.json", res)
}

// WriteLatestNamed writes a SignalResult as pretty JSON into dataDir/signals/<filename>.
func WriteLatestNamed(dataDir, filename string, res *SignalResult) (string, error) {
	dir := filepath.Join(dataDir, "signals")
	if err := ensureDir(dir); err != nil {
		return "", err
	}
	p := filepath.Join(dir, filename)
	b, err := json.MarshalIndent(res, "", "  ")
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(p, b, 0644); err != nil {
		return "", err
	}
	return p, nil
}

func AppendHistory(dataDir string, res *SignalResult) (string, error) {
	dir := filepath.Join(dataDir, "signals")
	if err := ensureDir(dir); err != nil {
		return "", err
	}
	p := filepath.Join(dir, "signals_1h_history.jsonl")
	return AppendJSONL(p, res)
}

// AppendJSONL appends an object as a single JSON line into the given path.
// It creates the parent directory automatically.
func AppendJSONL(path string, v interface{}) (string, error) {
	path = filepath.Clean(path)
	dir := filepath.Dir(path)
	if err := ensureDir(dir); err != nil {
		return "", err
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return "", err
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	enc := json.NewEncoder(w)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	if err := w.Flush(); err != nil {
		return "", err
	}
	return path, nil
}
