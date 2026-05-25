package models

import "time"

type Meeting struct {
	ID        uint      `gorm:"primaryKey"`
	Date      time.Time `gorm:"type:date;not null;index"`
	Title     string    `gorm:"type:text;not null;default:''"`
	CreatedBy string    `gorm:"type:text;not null"`
	CreatedAt time.Time `gorm:"autoCreateTime"`
	UpdatedAt time.Time `gorm:"autoUpdateTime"`
}

type MeetingAttendance struct {
	ID         uint `gorm:"primaryKey"`
	MeetingID  uint `gorm:"not null;index"`
	AttendeeID uint `gorm:"not null;index"`
	Present    bool `gorm:"not null;default:false"`
	OrderIdx   int  `gorm:"not null;default:0;column:order_idx"`
}

type MeetingProject struct {
	ID        uint   `gorm:"primaryKey"`
	MeetingID uint   `gorm:"not null;index"`
	Name      string `gorm:"type:text;not null"`
	LeaderID  *uint  `gorm:"index"`
	OrderIdx  int    `gorm:"not null;default:0;column:order_idx"`
}

type ProjectUpdate struct {
	ID               uint   `gorm:"primaryKey"`
	MeetingProjectID uint   `gorm:"not null;index"`
	Text             string `gorm:"type:text;not null;default:''"`
	OwnerID          *uint  `gorm:"index"`
	OrderIdx         int    `gorm:"not null;default:0;column:order_idx"`
}
