package models

import "time"

type Attendee struct {
	ID        uint      `gorm:"primaryKey" json:"id"`
	Name      string    `gorm:"type:text;not null" json:"name"`
	Email     *string   `gorm:"type:text" json:"email,omitempty"`
	Active    bool      `gorm:"not null;default:true" json:"active"`
	CreatedAt time.Time `gorm:"autoCreateTime" json:"created_at"`
}
