// inventory-api is a small HTTP service that lists the stock of one store.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
)

type item struct {
	SKU   string `json:"sku"`
	Name  string `json:"name"`
	Stock int    `json:"stock"`
}

var items = []item{
	{SKU: "BK-1001", Name: "The Phoenix Project", Stock: 12},
	{SKU: "BK-1002", Name: "Site Reliability Engineering", Stock: 4},
	{SKU: "BK-1003", Name: "Kubernetes Up and Running", Stock: 0},
}

func main() {
	store := os.Getenv("STORE_NAME")
	if store == "" {
		log.Fatal("STORE_NAME is not set; refusing to start")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"service": "inventory-api",
			"store":   store,
			"items":   items,
		})
	})

	log.Printf("inventory-api for store %q listening on :%s", store, port)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
