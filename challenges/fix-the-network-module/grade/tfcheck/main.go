// tfcheck parses the submitted module with Terraform's HCL parser (the same
// version as the Terraform the grader runs) and lists what it may not have.
//
//	tfcheck FILE...
//
// Prints one line per problem and exits 1 if there are any, 0 if there are
// none, and 2 if a file cannot be read.
//
// The rules, and why:
//
//   - Only variable, locals, resource and output blocks, and only aws_vpc and
//     aws_subnet resources. A provider block or a terraform_remote_state data
//     source can make Terraform start a program through the AWS SDK's
//     credential_process.
//   - No file or path functions (file, templatefile, templatestring,
//     abspath, ...), no provider functions, and no path, terraform or self
//     references. The grader's test with random IDs only works if the module
//     cannot read files, and a module that can tell the grader's machine or
//     its mock provider apart could act differently there.
//   - From aws_vpc and aws_subnet, only id, cidr_block, availability_zone and
//     vpc_id may be read, and a resource may only be used whole as the
//     collection of a for expression, in a splat that reads one of those
//     attributes, or in keys() and length(). The grader checks the ID outputs
//     with a mock provider, which makes up every other computed attribute (an
//     arn, an owner_id). A module that read them could notice the mock and
//     return the right IDs only there.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclsyntax"
	"github.com/zclconf/go-cty/cty"
)

var (
	allowedBlocks    = map[string]bool{"variable": true, "locals": true, "resource": true, "output": true}
	allowedResources = map[string]bool{"aws_vpc": true, "aws_subnet": true}
	allowedAttrs     = map[string]bool{"id": true, "cidr_block": true, "availability_zone": true, "vpc_id": true}
	freeRoots        = map[string]bool{"var": true, "local": true, "each": true, "count": true}
	bannedFunctions  = map[string]bool{
		"file": true, "fileexists": true, "fileset": true, "filebase64": true, "filebase64sha256": true,
		"filebase64sha512": true, "filemd5": true, "filesha1": true, "filesha256": true, "filesha512": true,
		"templatefile": true, "templatestring": true, "abspath": true, "pathexpand": true,
	}
	// References here are not values: Terraform only reads which objects they name.
	notValues = map[string]bool{
		"resource.depends_on": true, "resource.provider": true, "output.depends_on": true, "variable.type": true,
		"resource.lifecycle.ignore_changes": true, "resource.lifecycle.replace_triggered_by": true,
	}
)

type checker struct {
	file     string
	problems []string
	// Resources with count or for_each, by "type.name": their first index
	// picks an instance. On other resources it picks an attribute.
	repeated map[string]bool
}

func (c *checker) add(rng hcl.Range, format string, args ...any) {
	c.problems = append(c.problems, fmt.Sprintf("%s line %d %s", c.file, rng.Start.Line, fmt.Sprintf(format, args...)))
}

// scope holds the names bound by the for expressions around an expression.
// true means the name holds a resource (an instance of aws_subnet, say).
type scope map[string]bool

func (s scope) with(names map[string]bool) scope {
	out := scope{}
	for k, v := range s {
		out[k] = v
	}
	for k, v := range names {
		if k != "" {
			out[k] = v
		}
	}
	return out
}

func short(s string) string {
	if r := []rune(s); len(r) > 60 {
		return string(r[:57]) + "..."
	}
	return s
}

// describe writes a traversal without brackets, which a PR comment drops.
func describe(t hcl.Traversal) string {
	var parts []string
	for _, step := range t {
		switch s := step.(type) {
		case hcl.TraverseRoot:
			parts = append(parts, s.Name)
		case hcl.TraverseAttr:
			parts = append(parts, s.Name)
		case hcl.TraverseIndex:
			if key, ok := stringKey(s); ok {
				parts = append(parts, key)
			}
		}
	}
	return short(strings.Join(parts, "."))
}

func stringKey(step hcl.TraverseIndex) (string, bool) {
	if !step.Key.IsKnown() || step.Key.IsNull() || step.Key.Type() != cty.String {
		return "", false
	}
	return step.Key.AsString(), true
}

// isResource reports whether e names a whole resource, such as aws_subnet.public.
func isResource(e hclsyntax.Expression, sc scope) bool {
	st, ok := e.(*hclsyntax.ScopeTraversalExpr)
	if !ok || len(st.Traversal) != 2 {
		return false
	}
	root := st.Traversal.RootName()
	if _, shadowed := sc[root]; shadowed || !allowedResources[root] {
		return false
	}
	_, ok = st.Traversal[1].(hcl.TraverseAttr)
	return ok
}

// isResourceList is a whole resource, or values() of one.
func isResourceList(e hclsyntax.Expression, sc scope) bool {
	if call, ok := e.(*hclsyntax.FunctionCallExpr); ok && call.Name == "values" && len(call.Args) == 1 && !call.ExpandFinal {
		return isResource(call.Args[0], sc)
	}
	return isResource(e, sc)
}

// allowedStep is a relative traversal that reads one allowed attribute, as
// .id or ["id"].
func allowedStep(t hcl.Traversal) bool {
	if len(t) != 1 {
		return false
	}
	switch s := t[0].(type) {
	case hcl.TraverseAttr:
		return allowedAttrs[s.Name]
	case hcl.TraverseIndex:
		key, ok := stringKey(s)
		return ok && allowedAttrs[key]
	}
	return false
}

func (c *checker) traversal(t hcl.Traversal, rng hcl.Range, sc scope) {
	root := t.RootName()
	if holdsResource, bound := sc[root]; bound {
		if holdsResource && !allowedStep(t[1:]) {
			c.add(rng, "reads %s, which is more than the id, cidr_block, availability_zone or vpc_id of a resource", describe(t))
		}
		return
	}
	if freeRoots[root] {
		return
	}
	if !allowedResources[root] {
		c.add(rng, "refers to %s", describe(t))
		return
	}
	rest := t[1:]
	if len(rest) == 0 {
		c.add(rng, "refers to %s", root)
		return
	}
	name, ok := rest[0].(hcl.TraverseAttr)
	if !ok {
		c.add(rng, "refers to %s", describe(t))
		return
	}
	i := 1
	if i < len(rest) && c.repeated[root+"."+name.Name] {
		if _, ok := rest[i].(hcl.TraverseIndex); ok {
			i++
		}
	}
	if i == len(rest) {
		c.add(rng, "uses all of %s; read its id, cidr_block, availability_zone or vpc_id instead", describe(t))
		return
	}
	if !allowedStep(rest[i:]) {
		c.add(rng, "reads %s, which is more than the id, cidr_block, availability_zone or vpc_id of a resource", describe(t))
	}
}

func (c *checker) expr(e hclsyntax.Expression, sc scope) {
	switch e := e.(type) {
	case nil:
	case *hclsyntax.LiteralValueExpr:
	case *hclsyntax.ScopeTraversalExpr:
		c.traversal(e.Traversal, e.SrcRange, sc)
	case *hclsyntax.RelativeTraversalExpr:
		// aws_subnet.public[each.key].id
		if ix, ok := e.Source.(*hclsyntax.IndexExpr); ok && isResource(ix.Collection, sc) && c.repeatedResource(ix.Collection) {
			c.expr(ix.Key, sc)
			if !allowedStep(e.Traversal) {
				c.add(e.SrcRange, "reads more than the id, cidr_block, availability_zone or vpc_id of a resource")
			}
			return
		}
		c.expr(e.Source, sc)
	case *hclsyntax.FunctionCallExpr:
		name := strings.TrimPrefix(e.Name, "core::")
		if bannedFunctions[name] {
			c.add(e.NameRange, "calls %s()", name)
		} else if strings.Contains(name, "::") {
			c.add(e.NameRange, "calls the provider function %s()", short(name))
		}
		if (name == "keys" || name == "length") && len(e.Args) == 1 && !e.ExpandFinal && isResource(e.Args[0], sc) {
			return
		}
		for _, arg := range e.Args {
			c.expr(arg, sc)
		}
	case *hclsyntax.ForExpr:
		holdsResource := false
		if isResourceList(e.CollExpr, sc) {
			holdsResource = true
		} else {
			c.expr(e.CollExpr, sc)
		}
		inner := sc.with(map[string]bool{e.KeyVar: false, e.ValVar: holdsResource})
		c.expr(e.KeyExpr, inner)
		c.expr(e.ValExpr, inner)
		c.expr(e.CondExpr, inner)
	case *hclsyntax.SplatExpr:
		// aws_subnet.public[*].id
		if isResourceList(e.Source, sc) {
			if rel, ok := e.Each.(*hclsyntax.RelativeTraversalExpr); ok {
				if _, anon := rel.Source.(*hclsyntax.AnonSymbolExpr); anon && allowedStep(rel.Traversal) {
					return
				}
			}
			c.add(e.SrcRange, "reads more than the id, cidr_block, availability_zone or vpc_id of a resource")
			return
		}
		c.expr(e.Source, sc)
		c.expr(e.Each, sc)
	case *hclsyntax.AnonSymbolExpr:
	case *hclsyntax.IndexExpr:
		c.expr(e.Collection, sc)
		c.expr(e.Key, sc)
	case *hclsyntax.ConditionalExpr:
		c.expr(e.Condition, sc)
		c.expr(e.TrueResult, sc)
		c.expr(e.FalseResult, sc)
	case *hclsyntax.BinaryOpExpr:
		c.expr(e.LHS, sc)
		c.expr(e.RHS, sc)
	case *hclsyntax.UnaryOpExpr:
		c.expr(e.Val, sc)
	case *hclsyntax.ParenthesesExpr:
		c.expr(e.Expression, sc)
	case *hclsyntax.TupleConsExpr:
		for _, item := range e.Exprs {
			c.expr(item, sc)
		}
	case *hclsyntax.ObjectConsExpr:
		for _, item := range e.Items {
			c.expr(item.KeyExpr, sc)
			c.expr(item.ValueExpr, sc)
		}
	case *hclsyntax.ObjectConsKeyExpr:
		// A bare name as a key is a string, as in { Name = "x" }, unless it
		// is in parentheses. Same rule as ObjectConsKeyExpr.Value.
		if !e.ForceNonLiteral && hcl.ExprAsKeyword(e.Wrapped) != "" {
			return
		}
		c.expr(e.Wrapped, sc)
	case *hclsyntax.TemplateExpr:
		for _, part := range e.Parts {
			c.expr(part, sc)
		}
	case *hclsyntax.TemplateJoinExpr:
		c.expr(e.Tuple, sc)
	case *hclsyntax.TemplateWrapExpr:
		c.expr(e.Wrapped, sc)
	default:
		c.add(e.Range(), "has an expression the grader cannot check")
	}
}

func (c *checker) body(b *hclsyntax.Body, path string) {
	names := make([]string, 0, len(b.Attributes))
	for name := range b.Attributes {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !notValues[path+"."+name] {
			c.expr(b.Attributes[name].Expr, scope{})
		}
	}
	for _, block := range b.Blocks {
		c.body(block.Body, path+"."+block.Type)
	}
}

func (c *checker) repeatedResource(e hclsyntax.Expression) bool {
	t := e.(*hclsyntax.ScopeTraversalExpr).Traversal
	return c.repeated[t.RootName()+"."+t[1].(hcl.TraverseAttr).Name]
}

func parseFile(name string, src []byte) (*hclsyntax.Body, string) {
	file, diags := hclsyntax.ParseConfig(src, filepath.Base(name), hcl.InitialPos)
	for _, d := range diags {
		if d.Severity == hcl.DiagError {
			line := 0
			if d.Subject != nil {
				line = d.Subject.Start.Line
			}
			return nil, fmt.Sprintf("%s line %d does not parse: %s", filepath.Base(name), line, d.Summary)
		}
	}
	return file.Body.(*hclsyntax.Body), ""
}

// repeatedResources finds the resources that have count or for_each.
func repeatedResources(bodies []*hclsyntax.Body) map[string]bool {
	out := map[string]bool{}
	for _, body := range bodies {
		for _, block := range body.Blocks {
			if block.Type != "resource" || len(block.Labels) != 2 {
				continue
			}
			_, count := block.Body.Attributes["count"]
			_, forEach := block.Body.Attributes["for_each"]
			out[block.Labels[0]+"."+block.Labels[1]] = count || forEach
		}
	}
	return out
}

// check checks the files of one module, given as name and contents.
func check(files map[string][]byte) []string {
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	var problems []string
	bodies := map[string]*hclsyntax.Body{}
	for _, name := range names {
		body, problem := parseFile(name, files[name])
		if problem != "" {
			problems = append(problems, problem)
			continue
		}
		bodies[name] = body
	}
	all := make([]*hclsyntax.Body, 0, len(bodies))
	for _, name := range names {
		if body, ok := bodies[name]; ok {
			all = append(all, body)
		}
	}
	repeated := repeatedResources(all)
	for _, name := range names {
		if body, ok := bodies[name]; ok {
			c := &checker{file: filepath.Base(name), repeated: repeated}
			c.checkBody(body)
			problems = append(problems, c.problems...)
		}
	}
	return problems
}

func (c *checker) checkBody(body *hclsyntax.Body) {
	for _, attr := range body.Attributes {
		c.add(attr.SrcRange, "has an argument outside a block")
	}
	for _, block := range body.Blocks {
		if !allowedBlocks[block.Type] {
			c.add(block.TypeRange, "has a %s block", short(block.Type))
			continue
		}
		if block.Type == "resource" && len(block.Labels) > 0 && !allowedResources[block.Labels[0]] {
			c.add(block.TypeRange, "has a resource of type %s", short(block.Labels[0]))
			continue
		}
		c.body(block.Body, block.Type)
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: tfcheck FILE...")
		os.Exit(2)
	}
	files := map[string][]byte{}
	for _, name := range os.Args[1:] {
		src, err := os.ReadFile(name)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		files[name] = src
	}
	problems := check(files)
	for _, p := range problems {
		fmt.Println(strings.ReplaceAll(p, "\n", " "))
	}
	if len(problems) > 0 {
		os.Exit(1)
	}
}
