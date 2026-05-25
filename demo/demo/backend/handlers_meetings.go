package main

import (
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const dateLayout = "2006-01-02"

// ---- wire types ----

type attendeeJSON struct {
	ID    uint    `json:"id"`
	Name  string  `json:"name"`
	Email *string `json:"email,omitempty"`
}

type attendanceJSON struct {
	AttendeeID uint `json:"attendee_id"`
	Present    bool `json:"present"`
}

type updateJSON struct {
	ID       uint   `json:"id,omitempty"`
	Text     string `json:"text"`
	OwnerID  *uint  `json:"owner_id,omitempty"`
	OrderIdx int    `json:"order_idx,omitempty"`
}

type projectJSON struct {
	ID       uint         `json:"id,omitempty"`
	Name     string       `json:"name"`
	LeaderID *uint        `json:"leader_id,omitempty"`
	OrderIdx int          `json:"order_idx,omitempty"`
	Updates  []updateJSON `json:"updates"`
}

type meetingJSON struct {
	ID         uint             `json:"id,omitempty"`
	Date       string           `json:"date"`
	Title      string           `json:"title"`
	CreatedBy  string           `json:"created_by,omitempty"`
	CreatedAt  string           `json:"created_at,omitempty"`
	UpdatedAt  string           `json:"updated_at,omitempty"`
	Attendance []attendanceJSON `json:"attendance"`
	Projects   []projectJSON    `json:"projects"`
}

type meetingSummaryJSON struct {
	ID           uint   `json:"id"`
	Date         string `json:"date"`
	Title        string `json:"title"`
	CreatedBy    string `json:"created_by"`
	CreatedAt    string `json:"created_at"`
	UpdatedAt    string `json:"updated_at"`
	PresentCount int    `json:"present_count"`
	TotalCount   int    `json:"total_count"`
}

// ---- helpers ----

func bundleToJSON(b *meetingBundle) meetingJSON {
	updatesByProject := make(map[uint][]updateJSON, len(b.Projects))
	for _, u := range b.Updates {
		updatesByProject[u.MeetingProjectID] = append(updatesByProject[u.MeetingProjectID], updateJSON{
			ID:       u.ID,
			Text:     u.Text,
			OwnerID:  u.OwnerID,
			OrderIdx: u.OrderIdx,
		})
	}
	att := make([]attendanceJSON, 0, len(b.Attendances))
	for _, a := range b.Attendances {
		att = append(att, attendanceJSON{AttendeeID: a.AttendeeID, Present: a.Present})
	}
	projs := make([]projectJSON, 0, len(b.Projects))
	for _, p := range b.Projects {
		// Force a non-nil slice so JSON encodes [] instead of null when the
		// project has no updates. Callers iterate over this on the wire.
		updates := updatesByProject[p.ID]
		if updates == nil {
			updates = []updateJSON{}
		}
		projs = append(projs, projectJSON{
			ID:       p.ID,
			Name:     p.Name,
			LeaderID: p.LeaderID,
			OrderIdx: p.OrderIdx,
			Updates:  updates,
		})
	}
	return meetingJSON{
		ID:         b.Meeting.ID,
		Date:       b.Meeting.Date.Format(dateLayout),
		Title:      b.Meeting.Title,
		CreatedBy:  b.Meeting.CreatedBy,
		CreatedAt:  b.Meeting.CreatedAt.Format(time.RFC3339),
		UpdatedAt:  b.Meeting.UpdatedAt.Format(time.RFC3339),
		Attendance: att,
		Projects:   projs,
	}
}

func parseMeetingWrite(body io.Reader) (*meetingWrite, error) {
	var in meetingJSON
	if err := json.NewDecoder(body).Decode(&in); err != nil {
		return nil, err
	}
	date, err := time.Parse(dateLayout, in.Date)
	if err != nil {
		return nil, errBadDate
	}
	w := &meetingWrite{Date: date, Title: strings.TrimSpace(in.Title)}
	for _, a := range in.Attendance {
		w.Attendance = append(w.Attendance, struct {
			AttendeeID uint
			Present    bool
		}{AttendeeID: a.AttendeeID, Present: a.Present})
	}
	for _, p := range in.Projects {
		pw := struct {
			Name     string
			LeaderID *uint
			Updates  []struct {
				Text    string
				OwnerID *uint
			}
		}{Name: strings.TrimSpace(p.Name), LeaderID: p.LeaderID}
		for _, u := range p.Updates {
			pw.Updates = append(pw.Updates, struct {
				Text    string
				OwnerID *uint
			}{Text: strings.TrimSpace(u.Text), OwnerID: u.OwnerID})
		}
		w.Projects = append(w.Projects, pw)
	}
	return w, nil
}

var errBadDate = &badRequestError{msg: "invalid date (want YYYY-MM-DD)"}

type badRequestError struct{ msg string }

func (e *badRequestError) Error() string { return e.msg }

// ---- attendee handlers ----

func (app *App) handleListAttendees(w http.ResponseWriter, r *http.Request) {
	attendees, err := listActiveAttendees(app.db)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list attendees")
		return
	}
	out := make([]attendeeJSON, 0, len(attendees))
	for _, a := range attendees {
		out = append(out, attendeeJSON{ID: a.ID, Name: a.Name, Email: a.Email})
	}
	writeJSON(w, http.StatusOK, map[string]any{"attendees": out})
}

func (app *App) handleCreateAttendee(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name  string `json:"name"`
		Email string `json:"email"`
	}
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}
	name := strings.TrimSpace(in.Name)
	if name == "" {
		writeError(w, http.StatusBadRequest, "name is required")
		return
	}
	var email *string
	if e := strings.TrimSpace(in.Email); e != "" {
		email = &e
	}
	a, err := createAttendee(app.db, name, email)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create attendee")
		return
	}
	writeJSON(w, http.StatusOK, attendeeJSON{ID: a.ID, Name: a.Name, Email: a.Email})
}

// ---- meeting handlers ----

func (app *App) handleListMeetings(w http.ResponseWriter, r *http.Request) {
	summaries, err := listMeetingSummaries(app.db)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list meetings")
		return
	}
	out := make([]meetingSummaryJSON, 0, len(summaries))
	for _, s := range summaries {
		out = append(out, meetingSummaryJSON{
			ID: s.ID, Date: s.Date.Format(dateLayout), Title: s.Title,
			CreatedBy: s.CreatedBy,
			CreatedAt: s.CreatedAt.Format(time.RFC3339),
			UpdatedAt: s.UpdatedAt.Format(time.RFC3339),
			PresentCount: s.PresentCount, TotalCount: s.TotalCount,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"meetings": out})
}

func parseMeetingID(r *http.Request) (uint, error) {
	idStr := r.PathValue("id")
	n, err := strconv.ParseUint(idStr, 10, 64)
	if err != nil {
		return 0, err
	}
	return uint(n), nil
}

func (app *App) handleGetMeeting(w http.ResponseWriter, r *http.Request) {
	id, err := parseMeetingID(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid id")
		return
	}
	b, err := loadMeetingBundle(app.db, id)
	if err != nil {
		if isNotFound(err) {
			writeError(w, http.StatusNotFound, "meeting not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "failed to load meeting")
		return
	}
	writeJSON(w, http.StatusOK, bundleToJSON(b))
}

func (app *App) handleCreateMeeting(w http.ResponseWriter, r *http.Request) {
	wrt, err := parseMeetingWrite(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	b, err := upsertMeeting(app.db, 0, getUsername(r), wrt)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to save meeting")
		return
	}
	writeJSON(w, http.StatusOK, bundleToJSON(b))
}

func (app *App) handleUpdateMeeting(w http.ResponseWriter, r *http.Request) {
	id, err := parseMeetingID(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid id")
		return
	}
	wrt, err := parseMeetingWrite(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	b, err := upsertMeeting(app.db, id, getUsername(r), wrt)
	if err != nil {
		if isNotFound(err) {
			writeError(w, http.StatusNotFound, "meeting not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "failed to save meeting")
		return
	}
	writeJSON(w, http.StatusOK, bundleToJSON(b))
}

func (app *App) handleDeleteMeeting(w http.ResponseWriter, r *http.Request) {
	id, err := parseMeetingID(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid id")
		return
	}
	if err := deleteMeeting(app.db, id); err != nil {
		if isNotFound(err) {
			writeError(w, http.StatusNotFound, "meeting not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "failed to delete meeting")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"deleted": id})
}
