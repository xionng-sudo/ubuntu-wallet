package features

import (
	"encoding/json"
	"os"
)

func LoadFeatureColumns(path string) ([]string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cols []string
	if err := json.Unmarshal(b, &cols); err != nil {
		return nil, err
	}
	return cols, nil
}

func AlignToSchema(raw map[string]float64, cols []string) (aligned map[string]float64, computed int, missing int) {
	aligned = make(map[string]float64, len(cols))
	for _, c := range cols {
		if v, ok := raw[c]; ok {
			aligned[c] = safe(v)
			computed++
		} else {
			aligned[c] = 0.0
			missing++
		}
	}
	return aligned, computed, missing
}
