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
// series too.
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

	"github.com/prometheus/prometheus/model/labels"
	"github.com/prometheus/prometheus/promql/parser"
)

// The options promtool uses when it runs without feature flags, as the
// grader runs it (cmd/promtool/main.go).
var promql = parser.NewParser(parser.Options{ExperimentalDurationExpr: true})

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
			if rec, ok := m["record"]; ok {
				name = fmt.Sprint(rec)
			}
			labelMap, _ := m["labels"].(map[string]any)
			annotations, _ := m["annotations"].(map[string]any)
			out = append(out, rule{name: name, expr: m["expr"], labels: labelMap, annotations: annotations})
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
		return "names no metric"
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

func exprProblems(r rule, allowed map[string]bool) []string {
	text, ok := r.expr.(string)
	if !ok {
		// A number or a boolean reads no series. promtool decides if it is valid.
		return nil
	}
	expr, err := promql.ParseExpr(text)
	if err != nil {
		return []string{fmt.Sprintf("%s: the expression does not parse: %s", short(r.name, 40), short(err.Error(), 120))}
	}
	var problems []string
	parser.Inspect(expr, func(node parser.Node, _ []parser.Node) error {
		if vs, ok := node.(*parser.VectorSelector); ok {
			if why := selectorProblem(vs, allowed); why != "" {
				problems = append(problems, fmt.Sprintf("%s: the selector %s %s.", short(r.name, 40), short(vs.String(), 80), why))
			}
		}
		return nil
	})
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
	for _, r := range list {
		problems = append(problems, exprProblems(r, allowed)...)
		problems = append(problems, templateProblems(r)...)
	}
	return problems, nil
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
