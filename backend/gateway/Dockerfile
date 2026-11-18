# Two stages: compile with the full Go toolchain, then copy only the finished
# binary into a tiny image. The final image has no shell and no package
# manager, so there is very little in it to attack.

# ---- Stage 1: build ----
FROM golang:1.27-alpine AS build
WORKDIR /src

COPY . .
# CGO_ENABLED=0: a fully static binary that needs no C libraries at runtime.
# -trimpath and -ldflags="-s -w": no local paths or debug symbols (smaller).
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

# ---- Stage 2: run ----
# distroless/static: just CA certificates, timezone data and a non-root user.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/gateway"]
