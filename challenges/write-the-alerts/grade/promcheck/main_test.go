package main

import (
	"encoding/json"
	"strings"
	"testing"
)

var metrics = []string{"up", "http_requests_total", "http_request_duration_seconds_bucket"}

func doc(expr string, labels, annotations map[string]string) []byte {
	rules := []map[string]any{
		{"record": "job:requests:rate5m", "expr": `sum by (job) (rate(http_requests_total[5m]))`},
		{"alert": "CheckoutInstanceDown", "expr": expr, "labels": labels, "annotations": annotations},
	}
	data, _ := json.Marshal(map[string]any{"groups": []any{map[string]any{"name": "checkout", "rules": rules}}})
	return data
}

func problems(t *testing.T, expr string, labels, annotations map[string]string) []string {
	t.Helper()
	found, err := check(doc(expr, labels, annotations), metrics)
	if err != nil {
		t.Fatalf("%s: %v", expr, err)
	}
	return found
}

func TestSelectorsThatNameAnAllowedMetricPass(t *testing.T) {
	for _, expr := range []string{
		`up{job="checkout"} == 0`,
		`absent(up{job="checkout"})`,
		`sum(rate(http_requests_total{job="checkout", code=~"5.."}[5m])) / sum(rate(http_requests_total{job="checkout"}[5m])) > 0.05`,
		`histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job="checkout", route="/api/checkout"}[5m]))) > 0.5`,
		`{__name__="up", job="checkout"} == 0`,
		`{"up", job="checkout"} == 0`,
		`job:requests:rate5m{job="checkout"} > 1`,
		`max_over_time(up{job="checkout"}[10m:1m]) == 0`,
		`vector(1)`,
		`1`,
	} {
		if got := problems(t, expr, nil, nil); len(got) != 0 {
			t.Errorf("%s: %v", expr, got)
		}
	}
}

func TestSelectorsThatReadOtherSeriesAreFound(t *testing.T) {
	for _, expr := range []string{
		`up{job="checkout"} == 0 or on(instance) {alertname="CheckoutInstanceDown",alertstate="firing"}`,
		`{alertname="CheckoutInstanceDown"}`,
		`ALERTS{alertname="CheckoutInstanceDown"}`,
		`{"ALERTS", alertname="CheckoutInstanceDown"}`,
		`{"ALERTS"}`,
		`{__name__=~"ALERT.+"}`,
		`{__name__!="up", job="checkout"}`,
		`{__name__="ALERTS_FOR_STATE"}`,
		`up{job="checkout"} unless on() count({job=~".+"}) > 100`,
		`count_over_time({alertstate="firing"}[5m]) > 0`,
		`up{__name__="ALERTS"}`,
		`node_cpu_seconds_total > 0`,
		`absent(nothing_here) and up == 0`,
		`label_replace(up, "x", "$1", "instance", "(.*)") == 0 or {alertname!=""}`,
	} {
		if got := problems(t, expr, nil, nil); len(got) == 0 {
			t.Errorf("%s: no problem found", expr)
		}
	}
}

func TestClockAndLongLookbackAreFound(t *testing.T) {
	ok := []string{
		`max_over_time(up{job="checkout"}[1h]) == 0`,
		`max_over_time(max_over_time(up{job="checkout"}[30m])[30m:1m]) == 0`,
		`predict_linear(http_requests_total{job="checkout"}[30m], 3600) > 0`,
	}
	bad := []string{
		`up{job="checkout"} == 0 and on() (vector(time()) < 3600)`,
		`up{job="checkout"} == 0 and on() hour() < 24`,
		`up{job="checkout"} == 0 and on() day_of_week(vector(1)) >= 0`,
		`up{job="checkout"} == 0 unless on() timestamp(up{job="checkout"}) > 10000`,
		`up{job="checkout"} @ 100 == 0`,
		`up{job="checkout"} @ start() == 0`,
		`max_over_time(up{job="checkout"}[5m:1m] @ end()) == 0`,
		`up{job="checkout"} offset 1h == 0`,
		`max_over_time(up{job="checkout"}[5m:1m] offset 2h) == 0`,
		`up{job="checkout"} == 0 and on() absent_over_time(up{job="checkout"}[2h])`,
		`max_over_time(max_over_time(up{job="checkout"}[31m])[30m:1m]) == 0`,
		`max_over_time(up{job="checkout"}[30m+31m]) == 0`,
	}
	for _, expr := range ok {
		if got := problems(t, expr, nil, nil); len(got) != 0 {
			t.Errorf("%s: %v", expr, got)
		}
	}
	for _, expr := range bad {
		if got := problems(t, expr, nil, nil); len(got) == 0 {
			t.Errorf("%s: no problem found", expr)
		}
	}
}

func TestMessagesHaveNoBrackets(t *testing.T) {
	for _, expr := range []string{`{alertname="CheckoutInstanceDown"}`, `ALERTS{alertname="x"}`, `{__name__=~"up"}`} {
		for _, p := range problems(t, expr, nil, nil) {
			if strings.ContainsAny(p, "{}[]~") {
				t.Errorf("%s: %s", expr, p)
			}
		}
	}
}

func TestRecordingRuleNamedALERTSIsNotAllowed(t *testing.T) {
	rules := []map[string]any{
		{"record": "ALERTS", "expr": "vector(1)"},
		{"alert": "CheckoutInstanceDown", "expr": `ALERTS == 1`},
	}
	data, _ := json.Marshal(map[string]any{"groups": []any{map[string]any{"name": "g", "rules": rules}}})
	found, err := check(data, metrics)
	if err != nil || len(found) == 0 {
		t.Fatalf("got %v, %v", found, err)
	}
}

func TestTemplatesMayNotCallQuery(t *testing.T) {
	ok := []string{
		`Checkout instance {{ $labels.instance }} is down`,
		`{{ $value | humanizePercentage }} of checkout requests fail`,
		`{{ if gt $value 1.0 }}high{{ else }}low{{ end }}`,
		`{{ with $labels.instance }}{{ . | stripPort }}{{ end }}`,
		`a query of the past`,
		`{{ "query" }}`,
		`{{ .Labels.query }}`,
	}
	bad := []string{
		`{{ query "ALERTS" }}`,
		`{{ with query "up" }}{{ . | first | value }}{{ end }}`,
		`{{ range query "{alertstate=\"firing\"}" }}x{{ end }}`,
		`{{ define "x" }}{{ query "up" }}{{ end }}{{ template "x" }}`,
		`{{ if true }}{{ else }}{{ len (query "up") }}{{ end }}`,
		`{{ $x := query "up" }}`,
		`{{ (query "up") | first }}`,
		`{{ nosuchfunction }}`,
		`{{ unclosed`,
	}
	for _, text := range ok {
		if got := problems(t, `up == 0`, map[string]string{"x": text}, map[string]string{"summary": text}); len(got) != 0 {
			t.Errorf("%s: %v", text, got)
		}
	}
	for _, text := range bad {
		for _, where := range []string{"label", "annotation"} {
			labels, annotations := map[string]string{}, map[string]string{}
			if where == "label" {
				labels["severity"] = text
			} else {
				annotations["summary"] = text
			}
			got := problems(t, `up == 0`, labels, annotations)
			if len(got) == 0 || !strings.Contains(got[0], where) {
				t.Errorf("%s in a %s: %v", text, where, got)
			}
		}
	}
}

func TestGroupLabelsAreTemplatesToo(t *testing.T) {
	group := map[string]any{"name": "checkout", "labels": map[string]any{"team": `{{ with query "ALERTS" }}x{{ end }}`},
		"rules": []any{map[string]any{"alert": "CheckoutInstanceDown", "expr": "up == 0"}}}
	data, _ := json.Marshal(map[string]any{"groups": []any{group}})
	found, err := check(data, metrics)
	if err != nil || len(found) != 1 || !strings.HasPrefix(found[0], "group checkout: the template of the label team calls query") {
		t.Fatalf("got %v, %v", found, err)
	}
	group["labels"] = map[string]any{"severity": "page", "team": "checkout {{ $labels.instance }}"}
	data, _ = json.Marshal(map[string]any{"groups": []any{group}})
	if found, err := check(data, metrics); err != nil || len(found) != 0 {
		t.Fatalf("got %v, %v", found, err)
	}
}

func TestExperimentalSyntaxIsRefusedLikePromtool(t *testing.T) {
	for _, expr := range []string{`info(up)`, `limitk(1, up)`} {
		got := problems(t, expr, nil, nil)
		if len(got) == 0 || !strings.Contains(got[0], "does not parse") {
			t.Errorf("%s: %v", expr, got)
		}
	}
}

func TestUnreadableDocuments(t *testing.T) {
	for _, data := range []string{`[]`, `{"groups": 1}`, `{"groups": [1]}`, `{"groups": [{"rules": [1]}]}`, `not json`} {
		if _, err := check([]byte(data), metrics); err == nil {
			t.Errorf("%s: no error", data)
		}
	}
}
