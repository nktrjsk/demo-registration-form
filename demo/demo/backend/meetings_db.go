package main

import (
	"errors"
	"time"

	"backend/models"

	"gorm.io/gorm"
)

func listActiveAttendees(db *gorm.DB) ([]models.Attendee, error) {
	var out []models.Attendee
	err := db.Where("active = ?", true).Order("id asc").Find(&out).Error
	return out, err
}

func createAttendee(db *gorm.DB, name string, email *string) (*models.Attendee, error) {
	a := &models.Attendee{Name: name, Email: email, Active: true}
	if err := db.Create(a).Error; err != nil {
		return nil, err
	}
	return a, nil
}

// meetingBundle is the in-memory full meeting view, used for read responses.
type meetingBundle struct {
	Meeting     models.Meeting
	Attendances []models.MeetingAttendance
	Projects    []models.MeetingProject
	Updates     []models.ProjectUpdate // across all projects in this meeting
}

func loadMeetingBundle(db *gorm.DB, id uint) (*meetingBundle, error) {
	var m models.Meeting
	if err := db.First(&m, id).Error; err != nil {
		return nil, err
	}
	var att []models.MeetingAttendance
	if err := db.Where("meeting_id = ?", id).Order("order_idx asc, id asc").Find(&att).Error; err != nil {
		return nil, err
	}
	var projs []models.MeetingProject
	if err := db.Where("meeting_id = ?", id).Order("order_idx asc, id asc").Find(&projs).Error; err != nil {
		return nil, err
	}
	projIDs := make([]uint, 0, len(projs))
	for _, p := range projs {
		projIDs = append(projIDs, p.ID)
	}
	var updates []models.ProjectUpdate
	if len(projIDs) > 0 {
		if err := db.Where("meeting_project_id IN ?", projIDs).
			Order("meeting_project_id asc, order_idx asc, id asc").
			Find(&updates).Error; err != nil {
			return nil, err
		}
	}
	return &meetingBundle{Meeting: m, Attendances: att, Projects: projs, Updates: updates}, nil
}

// meetingWrite is the canonical input for create / replace operations.
type meetingWrite struct {
	Date     time.Time
	Title    string
	Attendance []struct {
		AttendeeID uint
		Present    bool
	}
	Projects []struct {
		Name     string
		LeaderID *uint
		Updates  []struct {
			Text    string
			OwnerID *uint
		}
	}
}

// upsertMeeting creates or replaces a meeting and all its nested rows in one tx.
// If id == 0, a new meeting is created. Otherwise the existing meeting's nested
// rows are wiped and rewritten (simpler and good enough for v1).
func upsertMeeting(db *gorm.DB, id uint, createdBy string, w *meetingWrite) (*meetingBundle, error) {
	var resultID uint
	err := db.Transaction(func(tx *gorm.DB) error {
		var m models.Meeting
		if id == 0 {
			m = models.Meeting{Date: w.Date, Title: w.Title, CreatedBy: createdBy}
			if err := tx.Create(&m).Error; err != nil {
				return err
			}
		} else {
			if err := tx.First(&m, id).Error; err != nil {
				return err
			}
			m.Date = w.Date
			m.Title = w.Title
			if err := tx.Save(&m).Error; err != nil {
				return err
			}
			// Wipe nested rows for this meeting.
			var oldProjs []models.MeetingProject
			if err := tx.Where("meeting_id = ?", m.ID).Find(&oldProjs).Error; err != nil {
				return err
			}
			oldProjIDs := make([]uint, 0, len(oldProjs))
			for _, p := range oldProjs {
				oldProjIDs = append(oldProjIDs, p.ID)
			}
			if len(oldProjIDs) > 0 {
				if err := tx.Where("meeting_project_id IN ?", oldProjIDs).
					Delete(&models.ProjectUpdate{}).Error; err != nil {
					return err
				}
			}
			if err := tx.Where("meeting_id = ?", m.ID).Delete(&models.MeetingProject{}).Error; err != nil {
				return err
			}
			if err := tx.Where("meeting_id = ?", m.ID).Delete(&models.MeetingAttendance{}).Error; err != nil {
				return err
			}
		}

		for i, row := range w.Attendance {
			rec := models.MeetingAttendance{
				MeetingID:  m.ID,
				AttendeeID: row.AttendeeID,
				Present:    row.Present,
				OrderIdx:   i,
			}
			if err := tx.Create(&rec).Error; err != nil {
				return err
			}
		}

		for i, p := range w.Projects {
			projRec := models.MeetingProject{
				MeetingID: m.ID,
				Name:      p.Name,
				LeaderID:  p.LeaderID,
				OrderIdx:  i,
			}
			if err := tx.Create(&projRec).Error; err != nil {
				return err
			}
			for j, u := range p.Updates {
				upd := models.ProjectUpdate{
					MeetingProjectID: projRec.ID,
					Text:             u.Text,
					OwnerID:          u.OwnerID,
					OrderIdx:         j,
				}
				if err := tx.Create(&upd).Error; err != nil {
					return err
				}
			}
		}

		resultID = m.ID
		return nil
	})
	if err != nil {
		return nil, err
	}
	return loadMeetingBundle(db, resultID)
}

func deleteMeeting(db *gorm.DB, id uint) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var projs []models.MeetingProject
		if err := tx.Where("meeting_id = ?", id).Find(&projs).Error; err != nil {
			return err
		}
		projIDs := make([]uint, 0, len(projs))
		for _, p := range projs {
			projIDs = append(projIDs, p.ID)
		}
		if len(projIDs) > 0 {
			if err := tx.Where("meeting_project_id IN ?", projIDs).
				Delete(&models.ProjectUpdate{}).Error; err != nil {
				return err
			}
		}
		if err := tx.Where("meeting_id = ?", id).Delete(&models.MeetingProject{}).Error; err != nil {
			return err
		}
		if err := tx.Where("meeting_id = ?", id).Delete(&models.MeetingAttendance{}).Error; err != nil {
			return err
		}
		res := tx.Delete(&models.Meeting{}, id)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return gorm.ErrRecordNotFound
		}
		return nil
	})
}

// meetingSummary is a lightweight row for the listing endpoint.
type meetingSummary struct {
	ID           uint
	Date         time.Time
	Title        string
	CreatedBy    string
	CreatedAt    time.Time
	UpdatedAt    time.Time
	PresentCount int
	TotalCount   int
}

func listMeetingSummaries(db *gorm.DB) ([]meetingSummary, error) {
	var meetings []models.Meeting
	if err := db.Order("date desc, id desc").Find(&meetings).Error; err != nil {
		return nil, err
	}
	out := make([]meetingSummary, 0, len(meetings))
	for _, m := range meetings {
		var total int64
		var present int64
		if err := db.Model(&models.MeetingAttendance{}).Where("meeting_id = ?", m.ID).Count(&total).Error; err != nil {
			return nil, err
		}
		if err := db.Model(&models.MeetingAttendance{}).Where("meeting_id = ? AND present = ?", m.ID, true).Count(&present).Error; err != nil {
			return nil, err
		}
		out = append(out, meetingSummary{
			ID: m.ID, Date: m.Date, Title: m.Title,
			CreatedBy: m.CreatedBy, CreatedAt: m.CreatedAt, UpdatedAt: m.UpdatedAt,
			PresentCount: int(present), TotalCount: int(total),
		})
	}
	return out, nil
}

func isNotFound(err error) bool { return errors.Is(err, gorm.ErrRecordNotFound) }
