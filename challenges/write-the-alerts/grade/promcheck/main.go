// promcheck reads the rules that check_rules.py wrote as JSON and lists the
// series each rule may not read.
//
//	promcheck RULES_JSON METRIC...
//
// Every series selector in an expression must name one of the given metrics,
// or a recording rule in the file, with an exact match on the metric name.
// The expressions are parsed by the same PromQL parser as promtool, so a
// selector cannot hide from the check: {alertname="X"} with no metric name,
// {"ALERTS"}, {__name__=~"ALERT.*"} and ALERTS itself are all found. The
// templates in labels and annotations may not call query, which would read
// series too. Label values must be plain text, not templates.
//
// A rule also may not read the clock (time(), hour(), ...), use @ or offset,
// or look back more than an hour, also through recording rules, and a
// recording rule may not read itself. The grader's scenarios have a random
// date, every metric and more than an hour of history, so within these limits
// a rule cannot tell a test from production.
//
// Prints one line per problem and exits 1 if there are any, 0 if there are
// none, and 2 if the file cannot be read.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"text/template"
	"text/template/parse"
	"time"

	"github.com/prometheus/prometheus/model/labels"
	"github.com/prometheus/prometheus/promql/parser"
)

// The options promtool uses when it runs without feature flags, as the
// grader runs it (cmd/promtool/main.go).
var promql = parser.NewParser(parser.Options{ExperimentalDurationExpr: true})

// MaxLookback is how far back a rule may read: an hour of ranges and
// subqueries, plus the 5 minutes an instant selector looks back, counted
// through the recording rules it reads. The scenarios in gen_tests.py have
// more history than this.
const (
	MaxLookback   = time.Hour + LookbackDelta
	LookbackDelta = 5 * time.Minute
)

var clockFunctions = map[string]bool{
	"time": true, "timestamp": true, "day_of_month": true, "day_of_week": true, "day_of_year": true,
	"days_in_month": true, "hour": true, "minute": true, "month": true, "year": true,
}

// The names in Prometheus's template function map (template/template.go),
// and the variables it defines before every alert template
// (model/rulefmt/rulefmt.go). Only the names matter for parsing.
var templateFuncs = template.FuncMap{}

const templateDefs = "{{$labels := .Labels}}{{$externalLabels := .ExternalLabels}}{{$externalURL := .ExternalURL}}{{$value := .Value}}"

func init() {
	for _, name := range []string{
		"args", "externalURL", "first", "graphLink", "humanize", "humanize1024", "humanizeDuration",
		"humanizePercentage", "humanizeTimestamp", "label", "match", "now", "parseDuration", "pathPrefix",
		"query", "reReplaceAll", "safeHtml", "sortByLabel", "stripDomain", "stripPort", "strvalue",
		"tableLink", "title", "tmpl", "toDuration", "toLower", "toTime", "toUpper", "urlQueryEscape", "value",
	} {
		templateFuncs[name] = func(...any) any { return nil }
	}
}

type rule struct {
	name        string
	record      string
	expr        any
	labels      map[string]any
	annotations map[string]any
}

func rules(doc any) ([]rule, error) {
	root, ok := doc.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("not a mapping")
	}
	groups, ok := root["groups"].([]any)
	if !ok {
		return nil, fmt.Errorf("no list of groups")
	}
	var out []rule
	for _, g := range groups {
		group, ok := g.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("a group is not a mapping")
		}
		list, ok := group["rules"].([]any)
		if !ok {
			return nil, fmt.Errorf("a group has no list of rules")
		}
		// Prometheus adds the group's labels to every alert and expands them
		// as templates too (rules/manager.go).
		if groupLabels, ok := group["labels"].(map[string]any); ok {
			out = append(out, rule{name: "group " + fmt.Sprint(group["name"]), labels: groupLabels})
		}
		for _, r := range list {
			m, ok := r.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("a rule is not a mapping")
			}
			name := fmt.Sprint(m["alert"])
			record, _ := m["record"].(string)
			if rec, ok := m["record"]; ok {
				name = fmt.Sprint(rec)
			}
			labelMap, _ := m["labels"].(map[string]any)
			annotations, _ := m["annotations"].(map[string]any)
			out = append(out, rule{name: name, record: record, expr: m["expr"], labels: labelMap, annotations: annotations})
		}
	}
	return out, nil
}

func recorded(doc any) []string {
	var names []string
	root, _ := doc.(map[string]any)
	groups, _ := root["groups"].([]any)
	for _, g := range groups {
		group, _ := g.(map[string]any)
		list, _ := group["rules"].([]any)
		for _, r := range list {
			if m, ok := r.(map[string]any); ok {
				if rec, ok := m["record"].(string); ok {
					names = append(names, rec)
				}
			}
		}
	}
	return names
}

func short(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n-3]) + "..."
	}
	return s
}

// selectorProblem says why a selector may not be used, or "" if it may.
func selectorProblem(vs *parser.VectorSelector, allowed map[string]bool) string {
	var names []*labels.Matcher
	for _, m := range vs.LabelMatchers {
		if m != nil && m.Name == labels.MetricName {
			names = append(names, m)
		}
	}
	if len(names) == 0 {
		var other []string
		for _, m := range vs.LabelMatchers {
			if m != nil {
				other = append(other, m.Name)
			}
		}
		return fmt.Sprintf("names no metric (it matches on %s)", short(strings.Join(other, ", "), 60))
	}
	for _, m := range names {
		if m.Type != labels.MatchEqual {
			return "picks the metric name with a pattern or a negative match"
		}
		if !allowed[m.Value] {
			return fmt.Sprintf("reads %s, which is not a metric from the brief or a recording rule in this file", short(m.Value, 40))
		}
	}
	return ""
}

// use is one selector of a rule: the metric it reads and how far back.
type use struct {
	metric   string
	lookback time.Duration
}

func exprProblems(r rule, allowed map[string]bool) ([]string, []use) {
	text, ok := r.expr.(string)
	if !ok {
		// A number or a boolean reads no series. promtool decides if it is valid.
		return nil, nil
	}
	expr, err := promql.ParseExpr(text)
	if err != nil {
		return []string{fmt.Sprintf("%s: the expression does not parse: %s", short(r.name, 40), short(err.Error(), 120))}, nil
	}
	var problems []string
	var uses []use
	add := func(format string, args ...any) {
		problems = append(problems, short(r.name, 40)+": "+fmt.Sprintf(format, args...))
	}
	parser.Inspect(expr, func(node parser.Node, path []parser.Node) error {
		switch n := node.(type) {
		case *parser.VectorSelector:
			if why := selectorProblem(n, allowed); why != "" {
				add("a selector %s.", why)
			}
			if n.Timestamp != nil || n.StartOrEnd != 0 {
				add("a selector uses the @ modifier.")
			}
			if n.OriginalOffset != 0 || n.OriginalOffsetExpr != nil {
				add("a selector uses offset.")
			}
			lookback := LookbackDelta
			if len(path) > 0 {
				if _, inRange := path[len(path)-1].(*parser.MatrixSelector); inRange {
					lookback = 0
				}
			}
			for _, outer := range path {
				switch o := outer.(type) {
				case *parser.MatrixSelector:
					lookback += o.Range
				case *parser.SubqueryExpr:
					lookback += o.Range
				}
			}
			for _, m := range n.LabelMatchers {
				if m != nil && m.Name == labels.MetricName && m.Type == labels.MatchEqual {
					uses = append(uses, use{metric: m.Value, lookback: lookback})
				}
			}
		case *parser.MatrixSelector:
			if n.RangeExpr != nil {
				add("a range is a duration expression; write it as a plain duration such as 5m.")
			}
		case *parser.SubqueryExpr:
			if n.Timestamp != nil || n.StartOrEnd != 0 {
				add("a subquery uses the @ modifier.")
			}
			if n.OriginalOffset != 0 || n.OriginalOffsetExpr != nil {
				add("a subquery uses offset.")
			}
			if n.RangeExpr != nil || n.StepExpr != nil {
				add("a subquery range or step is a duration expression; write it as a plain duration.")
			}
		case *parser.Call:
			if n.Func != nil && clockFunctions[n.Func.Name] {
				add("it calls %s(), which reads the clock.", n.Func.Name)
			}
		}
		return nil
	})
	return problems, uses
}

// lookbackProblems follows the recording rules each rule reads. A recording
// rule that reads itself, directly or through others, keeps state from one
// evaluation to the next, like a clock, and chained windows add up.
func lookbackProblems(list []rule, uses [][]use) []string {
	byName := map[string][]int{}
	for i, r := range list {
		if r.record != "" {
			byName[r.record] = append(byName[r.record], i)
		}
	}
	const (
		unseen = iota
		open
		done
	)
	state := make([]int, len(list))
	longest := make([]time.Duration, len(list))
	cyclic := make([]bool, len(list))
	var visit func(i int) (time.Duration, bool)
	visit = func(i int) (time.Duration, bool) {
		switch state[i] {
		case open:
			return 0, true
		case done:
			return longest[i], cyclic[i]
		}
		state[i] = open
		for _, u := range uses[i] {
			total := u.lookback
			for _, j := range byName[u.metric] {
				l, c := visit(j)
				cyclic[i] = cyclic[i] || c
				total = max(total, u.lookback+l)
			}
			longest[i] = max(longest[i], total)
		}
		state[i] = done
		return longest[i], cyclic[i]
	}
	var problems []string
	for i, r := range list {
		l, c := visit(i)
		switch {
		case c:
			problems = append(problems, fmt.Sprintf("%s: it reads a recording rule that reads itself, directly or through other rules.", short(r.name, 40)))
		case l > MaxLookback:
			problems = append(problems, fmt.Sprintf("%s: it looks back %s, with the recording rules it reads; the limit is an hour.", short(r.name, 40), l))
		}
	}
	return problems
}

// callsQuery reports whether a parsed template calls the query function.
func callsQuery(node parse.Node) bool {
	switch n := node.(type) {
	case nil:
		return false
	case *parse.ListNode:
		if n == nil {
			return false
		}
		for _, c := range n.Nodes {
			if callsQuery(c) {
				return true
			}
		}
		return false
	case *parse.ActionNode:
		return callsQuery(n.Pipe)
	case *parse.PipeNode:
		if n == nil {
			return false
		}
		for _, c := range n.Cmds {
			if callsQuery(c) {
				return true
			}
		}
		return false
	case *parse.CommandNode:
		for _, a := range n.Args {
			if callsQuery(a) {
				return true
			}
		}
		return false
	case *parse.IdentifierNode:
		return n.Ident == "query"
	case *parse.ChainNode:
		return callsQuery(n.Node)
	case *parse.IfNode:
		return callsQuery(n.Pipe) || callsQuery(n.List) || callsQuery(n.ElseList)
	case *parse.RangeNode:
		return callsQuery(n.Pipe) || callsQuery(n.List) || callsQuery(n.ElseList)
	case *parse.WithNode:
		return callsQuery(n.Pipe) || callsQuery(n.List) || callsQuery(n.ElseList)
	case *parse.TemplateNode:
		return callsQuery(n.Pipe)
	case *parse.TextNode, *parse.FieldNode, *parse.VariableNode, *parse.DotNode, *parse.NilNode,
		*parse.BoolNode, *parse.NumberNode, *parse.StringNode, *parse.CommentNode, *parse.BreakNode,
		*parse.ContinueNode:
		return false
	default:
		// A node this walk does not know could hide a call: refuse it.
		return true
	}
}

func templateProblem(text string) string {
	t, err := template.New("t").Funcs(templateFuncs).Option("missingkey=zero").Parse(templateDefs + text)
	if err != nil {
		return "does not parse as a template"
	}
	for _, tmpl := range t.Templates() {
		if tmpl.Tree != nil && callsQuery(tmpl.Tree.Root) {
			return "calls query"
		}
	}
	return ""
}

func templateProblems(r rule) []string {
	var problems []string
	for _, part := range []struct {
		kind   string
		values map[string]any
	}{{"label", r.labels}, {"annotation", r.annotations}} {
		keys := make([]string, 0, len(part.values))
		for k := range part.values {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			text, ok := part.values[k].(string)
			if !ok {
				continue
			}
			// A label template can read $externalURL, $externalLabels and now,
			// which differ between promtool and a real Prometheus, and labels
			// decide where an alert goes. These alerts need plain labels.
			if part.kind == "label" && strings.Contains(text, "{{") {
				problems = append(problems, fmt.Sprintf("%s: the label %s is a template; label values must be plain text.", short(r.name, 40), short(k, 40)))
				continue
			}
			if why := templateProblem(text); why != "" {
				problems = append(problems, fmt.Sprintf("%s: the template of the %s %s %s.", short(r.name, 40), part.kind, short(k, 40), why))
			}
		}
	}
	return problems
}

func check(data []byte, metrics []string) ([]string, error) {
	var doc any
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, err
	}
	list, err := rules(doc)
	if err != nil {
		return nil, err
	}
	allowed := map[string]bool{}
	for _, m := range append(metrics, recorded(doc)...) {
		allowed[m] = true
	}
	// Only Prometheus writes these. check_rules.py refuses a rule that records them.
	delete(allowed, "ALERTS")
	delete(allowed, "ALERTS_FOR_STATE")
	var problems []string
	uses := make([][]use, len(list))
	for i, r := range list {
		found, u := exprProblems(r, allowed)
		problems = append(problems, found...)
		problems = append(problems, templateProblems(r)...)
		uses[i] = u
	}
	return append(problems, lookbackProblems(list, uses)...), nil
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: promcheck RULES_JSON METRIC...")
		os.Exit(2)
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	problems, err := check(data, os.Args[2:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "cannot read the rules:", err)
		os.Exit(2)
	}
	for _, p := range problems {
		fmt.Println(strings.ReplaceAll(p, "\n", " "))
	}
	if len(problems) > 0 {
		os.Exit(1)
	}
}
