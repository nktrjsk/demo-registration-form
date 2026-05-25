package main

import (
	"fmt"
	"log"

	"backend/models"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

func mustInitDB() *gorm.DB {
	host := envOr("POSTGRES_HOST", "localhost")
	user := envOr("POSTGRES_USER", "admin")
	password := envOr("POSTGRES_PASSWORD", "")
	dbname := envOr("POSTGRES_DB", "postgres")
	port := envOr("POSTGRES_PORT", "5432")

	ensureDatabaseExists(host, port, user, password, dbname)

	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		host, port, user, password, dbname)

	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		log.Fatalf("failed to connect to database: %v", err)
	}

	sqlDB, err := db.DB()
	if err != nil {
		log.Fatalf("failed to get underlying sql.DB: %v", err)
	}
	sqlDB.SetMaxOpenConns(5)

	if err := db.AutoMigrate(
		&models.UserCounter{},
		&models.Attendee{},
		&models.Meeting{},
		&models.MeetingAttendance{},
		&models.MeetingProject{},
		&models.ProjectUpdate{},
	); err != nil {
		log.Fatalf("failed to migrate database: %v", err)
	}

	// gallery_images is managed via raw idempotent DDL rather than
	// AutoMigrate. GORM's column-diff path keeps emitting an unconditional
	// `DROP CONSTRAINT uni_gallery_images_key` whenever it thinks the
	// column previously had a unique constraint, which aborts the whole
	// migration on databases where that constraint name never existed
	// (SQLSTATE 42704). Plain CREATE-IF-NOT-EXISTS sidesteps that.
	const galleryImagesDDL = `
	CREATE TABLE IF NOT EXISTS gallery_images (
		id           BIGSERIAL    PRIMARY KEY,
		key          TEXT         NOT NULL,
		title        TEXT         NOT NULL,
		content_type TEXT         NOT NULL,
		size         BIGINT       NOT NULL,
		uploaded_by  TEXT         NOT NULL,
		created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
	)`
	if err := db.Exec(galleryImagesDDL).Error; err != nil {
		log.Fatalf("failed to create gallery_images table: %v", err)
	}
	// Drop any leftover constraint/index from prior half-migrated states so
	// the table converges on a single uniqueness mechanism we control.
	db.Exec(`ALTER TABLE gallery_images DROP CONSTRAINT IF EXISTS uni_gallery_images_key`)
	if err := db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_images_key ON gallery_images (key)`).Error; err != nil {
		log.Fatalf("failed to create gallery_images key index: %v", err)
	}

	return db
}

// ensureDatabaseExists connects to the default "postgres" database and creates
// the target database if it does not yet exist. The platform supplies the DB
// name via POSTGRES_DB but does not always pre-create it for new worktrees.
func ensureDatabaseExists(host, port, user, password, dbname string) {
	adminDSN := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=postgres sslmode=disable",
		host, port, user, password)
	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{Logger: nil})
	if err != nil {
		log.Printf("ensureDatabaseExists: cannot connect to admin db: %v", err)
		return
	}
	sqlDB, err := admin.DB()
	if err != nil {
		return
	}
	defer sqlDB.Close()

	var exists bool
	row := sqlDB.QueryRow("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = $1)", dbname)
	if err := row.Scan(&exists); err != nil {
		log.Printf("ensureDatabaseExists: probe failed: %v", err)
		return
	}
	if exists {
		return
	}
	// Database names cannot be bound via $1 in CREATE DATABASE; sanitize by
	// allow-listing identifier chars before interpolating.
	if !isSafeIdent(dbname) {
		log.Printf("ensureDatabaseExists: refusing to create db with unsafe name %q", dbname)
		return
	}
	if _, err := sqlDB.Exec("CREATE DATABASE \"" + dbname + "\""); err != nil {
		log.Printf("ensureDatabaseExists: CREATE DATABASE failed: %v", err)
		return
	}
	log.Printf("ensureDatabaseExists: created database %s", dbname)
}

func isSafeIdent(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		ok := (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '_'
		if !ok {
			return false
		}
	}
	return true
}

func listGalleryImages(db *gorm.DB) ([]models.GalleryImage, error) {
	var images []models.GalleryImage
	err := db.Order("created_at desc").Find(&images).Error
	return images, err
}

func getCount(db *gorm.DB, username string) (int, error) {
	var counter models.UserCounter
	err := db.Where("username = ?", username).First(&counter).Error
	if err == gorm.ErrRecordNotFound {
		return 0, nil
	}
	return counter.Count, err
}

func incrementCount(db *gorm.DB, username string) (int, error) {
	var counter models.UserCounter
	err := db.Where("username = ?", username).First(&counter).Error
	if err == gorm.ErrRecordNotFound {
		counter = models.UserCounter{Username: username, Count: 1}
		if err := db.Create(&counter).Error; err != nil {
			return 0, err
		}
		return 1, nil
	}
	if err != nil {
		return 0, err
	}
	counter.Count++
	if err := db.Save(&counter).Error; err != nil {
		return 0, err
	}
	return counter.Count, nil
}

func insertGalleryImage(db *gorm.DB, key, title, contentType string, size int, uploadedBy string) (*models.GalleryImage, error) {
	img := &models.GalleryImage{
		Key:         key,
		Title:       title,
		ContentType: contentType,
		Size:        size,
		UploadedBy:  uploadedBy,
	}
	if err := db.Create(img).Error; err != nil {
		return nil, err
	}
	return img, nil
}

func deleteGalleryImage(db *gorm.DB, key string) error {
	result := db.Where("key = ?", key).Delete(&models.GalleryImage{})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected == 0 {
		return gorm.ErrRecordNotFound
	}
	return nil
}

func galleryImageExists(db *gorm.DB, key string) bool {
	var count int64
	db.Model(&models.GalleryImage{}).Where("key = ?", key).Count(&count)
	return count > 0
}
