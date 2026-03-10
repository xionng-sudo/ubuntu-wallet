package features

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
)

func EnsureDir(dir string) error {
	return os.MkdirAll(dir, 0755)
}

func WriteLatest(dataDir string, snap *FeatureSnapshot) (string, error) {
	dir := filepath.Join(dataDir, "features")
	if err := EnsureDir(dir); err != nil {
		return "", err
	}
	p := filepath.Join(dir, "features_1h_latest.json")
	b, err := json.MarshalIndent(snap, "", "  ")
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(p, b, 0644); err != nil {
		return "", err
	}
	return p, nil
}

func AppendHistory(dataDir string, snap *FeatureSnapshot) (string, error) {
	dir := filepath.Join(dataDir, "features")
	if err := EnsureDir(dir); err != nil {
		return "", err
	}
	p := filepath.Join(dir, "features_1h_history.jsonl")

	f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return "", err
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	enc := json.NewEncoder(w)
	if err := enc.Encode(snap); err != nil {
		return "", err
	}
	if err := w.Flush(); err != nil {
		return "", err
	}
	return p, nil
}
