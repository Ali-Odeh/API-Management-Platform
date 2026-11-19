package middleware

import "net/http"

// statusWriter wraps a ResponseWriter to see what is sent: the status code
// and the number of body bytes. It embeds the ResponseWriter, so every
// method it does not override (Header, ...) works as before.
type statusWriter struct {
	http.ResponseWriter
	status int   // 0 until the answer starts
	bytes  int64 // body bytes written
}

func (s *statusWriter) WriteHeader(code int) {
	// 1xx answers (100 Continue, 103 Early Hints) come before the real one.
	if s.status == 0 && code >= 200 {
		s.status = code
	}
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusWriter) Write(b []byte) (int, error) {
	if s.status == 0 {
		s.status = http.StatusOK // Write without WriteHeader means 200
	}
	n, err := s.ResponseWriter.Write(b)
	s.bytes += int64(n)
	return n, err
}

// Flush passes flushes through, so streamed answers still stream.
func (s *statusWriter) Flush() {
	_ = http.NewResponseController(s.ResponseWriter).Flush()
}

// Unwrap lets http.ResponseController (used by the reverse proxy) reach the
// original writer's other abilities, such as taking over the connection.
func (s *statusWriter) Unwrap() http.ResponseWriter {
	return s.ResponseWriter
}

// started reports whether the answer has begun: after that, the status can
// no longer be changed.
func (s *statusWriter) started() bool {
	return s.status != 0
}

// finalStatus is the status the client got. A handler that wrote nothing
// gets Go's default, 200.
func (s *statusWriter) finalStatus() int {
	if s.status == 0 {
		return http.StatusOK
	}
	return s.status
}
